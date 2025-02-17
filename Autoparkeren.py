# -*- coding: utf-8 -*-
"""
Created on Sun Feb 11 16:11:21 2024

@author: info
"""

# streamlit run dashboard_vlog.py

import pandas as pd
import geopandas as gpd
import folium
from streamlit_folium import st_folium
import sqlite3
import os

import streamlit as st
from streamlit_extras.mandatory_date_range import date_range_picker
from streamlit_extras.stylable_container import stylable_container
import numpy as np
import plotly.graph_objects as go
from requests.exceptions import ReadTimeout

import branca
import requests
import json
import time
from datetime import datetime
from scipy.integrate import simpson
from numpy import trapz
import base64
from shapely.geometry import Point
from branca.element import Template, MacroElement
import threading
from queue import Queue
from pathlib import Path

# Google Drive file ID (Extracted from the shared link)
file_id = "1uTx4vMVtcVzZdCgg4qGnkiMdO-Rm44Uo"
gpkg_path = "geopackage_parkeren.gpkg"

# Function to download file
def download_gpkg_from_drive(file_id, destination):
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    response = requests.get(url, stream=True)
    
    if response.status_code == 200:
        with open(destination, "wb") as file:
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    file.write(chunk)
        print("✅ Download completed!")
    else:
        print("❌ Failed to download file.")

# Download only if the file does not exist
if not os.path.exists(gpkg_path):
    download_gpkg_from_drive(file_id, gpkg_path)

# Load the GeoPackage
kaartdata = gpd.read_file(gpkg_path, layer="statische_autoparkeerdata")

# Lock voor toegang tot de GeoPackage
db_lock = threading.Lock()

# Queue voor API-bevragingen die moeten worden weggeschreven naar de GeoPackage
write_queue = Queue()


# Maak de legenda als een HTML element met hardcoded kleuren en "Vrije plaatsen"
legend_template = """
{% macro html(this, kwargs) %}
<div id='maplegend' class='maplegend' 
    style='position: absolute; z-index: 9999; background-color: rgba(255, 255, 255, 0.5);
     border-radius: 6px; padding: 10px; font-size: 10.5px; right: 20px; top: 20px;'>     
<div class='legend-scale'>
  <ul class='legend-labels'>
    <li><span style='background: purple; opacity: 0.75;'></span>Vrije plaatsen < 40</li>
    <li><span style='background: red; opacity: 0.75;'></span>Vrije plaatsen 40-80</li>
    <li><span style='background: orange; opacity: 0.75;'></span>Vrije plaatsen 80-120</li>
    <li><span style='background: yellow; opacity: 0.75;'></span>Vrije plaatsen 120-160</li>
    <li><span style='background: green; opacity: 0.75;'></span>Vrije plaatsen >= 160</li>
  </ul>
</div>
</div> 
<style type='text/css'>
  .maplegend .legend-scale ul {margin: 0; padding: 0; color: #0f0f0f;}
  .maplegend .legend-scale ul li {list-style: none; line-height: 18px; margin-bottom: 1.5px;}
  .maplegend ul.legend-labels li span {float: left; height: 16px; width: 16px; margin-right: 4.5px;}
</style>
{% endmacro %}
"""


# Controleer of de kaart is veranderd (zoom/pan)
def check_data_changed(click_result):
    if click_result is not None:
        # Wanneer de laatste zoom/pan locatie verandert
        if click_result['center'] != st.session_state.center or click_result['zoom'] != st.session_state.zoom:
            return True
    return False

def percentile(n):
    def percentile_(x):
        return np.percentile(x, n)
    percentile_.__name__ = 'percentile_%s' % n
    return percentile_

def clean_gpkg_contents(gpkg_path, layers):
    # Connect to the GeoPackage
    conn = sqlite3.connect(gpkg_path)
    cursor = conn.cursor()
    
    for layer_name in layers:
        
        cursor.execute(f"DELETE FROM {layer_name}")
        cursor.execute("DELETE FROM gpkg_contents WHERE table_name = ?", (layer_name,))
        cursor.execute("DELETE FROM gpkg_geometry_columns WHERE table_name = ?", (layer_name,))
        cursor.execute(f"DROP TABLE IF EXISTS {layer_name}")

    # Commit changes and close the connection
    conn.commit()
    cursor.close()
    conn.close()

#clean_gpkg_contents(gpkg_path, ['results', 'resultsref'])
#print(ka)


username = 'info@t4technology.nl'
password = "r^npu^MwBoSwdCicf2MMI8elLuw2=kjyp@yF=+U)P=)GO=CSR^9sa)KF!bZaV5Gq"
#password = password.encode(encoding = 'ascii')
credentials = f"{username}:{password}"
encoded_credentials = base64.b64encode(credentials.encode()).decode()

headers = {
    "Authorization": f"Basic {encoded_credentials}",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Cache-Control": "no-cache",  # Zorg ervoor dat je altijd de laatste versie ophaalt
    "Connection": "keep-alive"
}

st.set_page_config(page_title="Autoparkeren", layout='wide')

# beide APIS's werken, maar bij de eerste krijg je ook direct lat en long mee
parkereninternetpath2= "https://npropendata.rdw.nl/parkingdata/v2"

def clear_session_state():
    for key in list(st.session_state.keys()):
        if key != "current_page":  # Behoud de variabele voor de huidige pagina
            del st.session_state[key]        

if "current_page" not in st.session_state:
    st.session_state.current_page = "Autoparkeren"

page = 'Autoparkeren'
# Wis de session state wanneer de pagina verandert
if st.session_state.current_page != page:
    clear_session_state()
    st.session_state.current_page = page

if 'has_run' not in st.session_state:
    st.session_state.has_run = False  # Zet de status op False bij het eerste bezoek
    
if 'actueel' not in st.session_state:
    st.session_state['actueel'] = 1

if 'invoer' not in st.session_state:
    st.session_state['invoer'] = []

if 'grafiekinputdata' not in st.session_state:
    st.session_state['grafiekinputdata'] = None

if 'auto_refresh' not in st.session_state:
    st.session_state['auto_refresh'] = True

def create_geopackage(file_path):
    # lege package maken
    df = pd.DataFrame(columns=['ID', 'Name', 'Operator', 'Capacity', 'Lat', 'Long', 'geometry'])
    
    # # Maak een lege GeoDataFrame, geef het een CRS en geometrie kolom
    df[['Lat', 'Long']] = df[['Lat', 'Long']].astype(float)
    df['Capacity'] = df['Capacity'].astype(int)
    gdf = gpd.GeoDataFrame(df, geometry='geometry', crs="EPSG:4326")
    gdf.to_file(gpkg_path, layer='statische_autoparkeerdata', driver="GPKG", mode='a')
    
    gdf = gpd.GeoDataFrame(pd.DataFrame(columns = ['ID', 'Name', 'Place', 'Starttime','Open', 'Full', 'LastUpdated', 'Capacity', 'Vacantspaces']))
    gdf[['Open', 'Full', 'LastUpdated', 'Capacity', 'Vacantspaces']] = gdf[['Open', 'Full', 'LastUpdated', 'Capacity', 'Vacantspaces']].astype(int)
    # # # Sla de lege GeoDataFrame op in een lege GeoPackage
    gdf.to_file(gpkg_path, layer='dynamische_autoparkeerdata', driver="GPKG", mode='a')
    
    gdf = gpd.read_file('Gemeente_Regio2015.shp')
    gdf.to_file(gpkg_path, layer='gemeenten', driver="GPKG", mode='w')
    time.sleep(5)

def check_and_create_geopackage(file_path):
    if not Path(file_path).exists():
        print(f"{file_path} bestaat niet. Het bestand wordt nu aangemaakt.")
        create_geopackage(file_path)
    else:
        print(f"{file_path} bestaat al.")
    

def dynamic_parking():
    

    response = requests.get(parkereninternetpath2, headers=headers)
    
    print(response)

    output = json.loads(response.text)

    # Filter garages met een dynamicDataUrl
    parking_list = output["ParkingFacilities"]  # Pak de lijst met parkeergarages
    
    filtered_garages = {g["identifier"]: g["name"] for g in parking_list if "dynamicDataUrl" in g and "identifier" in g and "name" in g}
    filtered_garages_list = [{"ID": k, "Name": v} for k, v in filtered_garages.items()]
    df = pd.DataFrame(filtered_garages_list)
    
    #gpd.GeoDataFrame(df).to_file(gpkg_path, layer = "dynamic_parking_facilities", driver="GPKG")
    dyna = gpd.GeoDataFrame(df)
    
    return dyna


def statische_data():

    park_data = []
    

    garages_gdf = gpd.read_file(gpkg_path, layer='dynamic_parking_facilities')
    filtered_garages = dict(zip(garages_gdf['ID'], garages_gdf['Name']))

    aanwezig = gpd.read_file(gpkg_path, layer='statische_autoparkeerdata')
    ids = aanwezig['ID'].tolist()  # Converteer de ID's naar een lijst
    filtered_garages = {key: value for key, value in filtered_garages.items() if key not in ids}
    
    while len(filtered_garages) > 0:

        #print(f"Verwerking gestart: {len(filtered_garages)} ID's in filtered_garages")

        for ID in filtered_garages:
            
            statpath = 'https://npropendata.rdw.nl/parkingdata/v2/static/'+ ID
            
            try:
                response = requests.get(statpath, headers=headers, timeout = 10)
            
                if response.status_code == 200:
                    
        
                    output = json.loads(response.text)
                    
                    #new_id = output.get('parkingFacilityInformation', {}).get('identifier', 'Unknown ID')
                    name = output.get('parkingFacilityInformation', {}).get('description', output.get('parkingFacilityInformation', {}).get('name', 'Unknown Name'))
                    operator = output.get('parkingFacilityInformation', {}).get('operator', {}).get('name', 'Unknown Operator')
                    
                    capacity = -1  # Stel een fallback waarde in
                    specifications = output.get('parkingFacilityInformation', {}).get('specifications', [])
                    if specifications and 'capacity' in specifications[0]:
                        try:
                            capacity = int(specifications[0]['capacity'])
                        except ValueError:
                            capacity = -1
                    
                    access = output.get('parkingFacilityInformation', {}).get('accessPoints', [])
                    lat, long = None, None  # Initialiseer met None als fallback
                    
                    
                    
                    for acc in access:
                        if acc.get('isVehicleEntrance', False) == True:  # Voor voertuigingangen
                            lat = acc.get('accessPointLocation', [{}])[0].get('latitude', None)
                            long = acc.get('accessPointLocation', [{}])[0].get('longitude', None)
                            if lat is not None and long is not None:  # Stop als we geldige coÃ¶rdinaten vinden
                                break
                    
                    # Als er geen coÃ¶rdinaten gevonden zijn bij voertuigingangen, kijk dan naar voetgangersingangen
                    if lat is None or long is None:  # Als geen coÃ¶rdinaten bij voertuigingangen
                        for acc in access:
                            if acc.get('isVehicleEntrance', False) == False:  # Voor voetgangersingangen
                                lat = acc.get('accessPointLocation', [{}])[0].get('latitude', None)
                                long = acc.get('accessPointLocation', [{}])[0].get('longitude', None)
                                if lat is not None and long is not None:  # Stop als we geldige coÃ¶rdinaten vinden
                                    break
            
                    lat = float(lat) if lat is not None else None
                    long = float(long) if long is not None else None
                    
                    # Voeg de gegevens toe aan de verzameling
                    park_data.append({
                        'ID': ID,
                        'Name': name,
                        'Operator': operator,
                        'Capacity': capacity,
                        'Lat': lat,
                        'Long': long
                    })
    
                    #print(f"Verwerkt: {ID} - {filtered_garages[ID]}")  # Print de ID en naam van de parkeerplaats
    
                
                    # Verwijder de ID uit de filtered_garages na verwerking
                    filtered_garages.pop(ID, None)
                    break
                time.sleep(1)

            except ReadTimeout:
                break  # Breek de loop af als er een timeout is



    gdf = pd.DataFrame(park_data, columns=['ID', 'Name', 'Operator', 'Capacity', 'Lat', 'Long'])    
        
    gdf['geometry'] = gdf.apply(lambda row: Point(row['Long'], row['Lat']), axis=1)
    
    gdf = gpd.GeoDataFrame(gdf, geometry='geometry')
    
    #gdf = gpd.GeoDataFrame(df, geometry = point)
    gdf = gdf.set_crs(4326)
    gdf = gdf.to_crs(28992)
    
    gdf['geometry'] = gdf['geometry'].buffer(50)
    gdf = gdf.to_crs(4326)
    
    # voorlopig alleen de koplopersteden
    cities_gdf = gpd.read_file(gpkg_path, layer='gemeenten')
    cities_of_interest = cities_gdf[cities_gdf['GMNAAM'].isin(['Almere', 'Apeldoorn', 'Amersfoort', 'Zwolle', 'Dordrecht', 'Helmond', 'Heerlen'])]
    # Selecteer garages die binnen de steden liggen
    garages_within_cities = gdf[gdf.geometry.within(cities_of_interest.union_all())]
    
    #garages_within_cities.to_file(gpkg_path, layer = 'statische_autoparkeerdata', driver = "GPKG", mode = 'a')
    return garages_within_cities

# Simuleer de API-aanroepen (vervang dit door echte API-aanroepen)
def call_api_weekly():
    while True:

        time.sleep(7 * 24 * 60 * 60)  # Wacht een week (7 dagen)

        # test of er een geopackage is en maak anders aan
        #check_and_create_geopackage(gpkg_path)
        
        dyna = dynamic_parking()
        write_queue.put((dyna, "dynamic_parking_facilities"))

        garages_within_cities = statische_data()
        write_queue.put((garages_within_cities, "statische_autoparkeerdata"))

    
def nonstop_dynamische_data():
    
    datetimeformat = "%Y-%m-%d %H:%M:%S"
    
    data = []
    #vacancy_data = {}
    
    garages_gdf = gpd.read_file(gpkg_path, layer='statische_autoparkeerdata')
    filtered_garages = {row['ID']: row['Name'] for idx, row in garages_gdf.iterrows()}

    # tijdelijk alleen Apeldoorn
    # filtered_garages = {'c8def33d-6bbf-4162-a9bc-27bc79430d0d' : 'Parkeergarage Marktplein',\
    #         'd381c28b-2cf8-48b6-b0f8-8166c08cf25f' : 'Parkeergarage Koningshaven-Centrum',\
    #         '62075c53-5284-47d4-8909-3872721380d7' : 'Parkeergarage Brinklaan (op werkdagen zijn 125 plaatsen gereserveerd)',\
    #         'f3dca9dd-12f1-4890-9077-ff24322d5160' : 'Oranjerie', \
    #         'bbca34c0-27d4-4d50-8544-612826339d33' : 'Museum Centrum',\
    #         'e556a717-480e-433d-b87b-0f37140076a6' : 'Parkeergarage Orpheus',\
    #         'dacca715-2cf3-400f-96c3-367f32f994e9' : 'Parkeergarage Anklaar', \
    #         'd713179a-5257-46e3-a989-705291add0c1' : 'P+R Laan van de Mensenrechten (Apeldoorn)',\
    #         'd553e1ea-1d7f-44a4-ba4b-9439ce4f4d1b' : 'P+R Laan van de Mensenrechten', \
    #         '33ac0b4d-9a9d-4c39-85df-13293292f04b' : 'afrit 24 Apeldoorn (A50)'}

    t = 0
    while len(filtered_garages) > 0 and t<5:

        #print(f"Verwerking gestart: {len(filtered_garages)} ID's in filtered_garages")

        for ID in filtered_garages:

            dynpath = 'https://npropendata.rdw.nl/parkingdata/v2/dynamic/'+ ID
            
            place = 'Gemeente Apeldoorn'    # DIT NOG VERANDEREN!
            name = filtered_garages[ID]
            
            try:
                response = requests.get(dynpath, headers=headers, timeout = 10)
            
                if response.status_code == 200:
    
                    output = json.loads(response.text)
                            
                    try:
                        output = output['parkingFacilityDynamicInformation']['facilityActualStatus']
                    except KeyError:
                        output = output.get('facilityActualStatus', output.get('actualStatus', {}))              
                    
                    timestamp = datetime.now().strftime(datetimeformat)
                    
                    
                    # Verkrijg de waarden (met fallback als ze ontbreken)
                    parkopen = output.get('open', 0)
                    parkvol = output.get('full', 0)
                    update = output.get('lastUpdated', -1)
                    actcap = output.get('parkingCapacity', -1)
                    actvac = output.get('vacantSpaces', -1)
                    
         
                    # Voeg de verzamelde data toe aan de lijst
                    data.append({
                        'ID': ID,
                        'Name': name,
                        'Place': place,
                        'Starttime': timestamp,
                        'Open': int(parkopen),
                        'Full': int(parkvol),
                        'LastUpdated': int(update),
                        'Capacity': int(actcap),
                        'Vacantspaces': int(actvac)})
                    
                    # Verwijder de ID uit de filtered_garages na verwerking
                    #print(f"Verwerkt: {ID} - {filtered_garages[ID]}")  # Print de ID en naam van de parkeerplaats
                    filtered_garages.pop(ID, None)
                    t = 0
                    break

                time.sleep(1)

            except ReadTimeout:
                break  # Breek de loop af als er een timeout is
                    
                    #vacancy_data[ID] = actvac
        t = t+1

    df = pd.DataFrame(data, columns=['ID', 'Name', 'Place', 'Starttime', 'Open', 'Full', 'LastUpdated', 'Capacity', 'Vacantspaces'])
    
    # Schrijf de verzamelde data naar de GeoPackage (mode 'w' overschrijft de laag)
    #gpd.GeoDataFrame(df).to_file(gpkg_path, layer = "dynamische_autoparkeerdata", driver="GPKG", mode = 'a')
    nonstop = gpd.GeoDataFrame(df)
    return nonstop

#while True:
#    nonstop_dynamische_data()
#    print('ik ga slapen')
#    time.sleep(300)

def verwerken_dynamische_data():
    
    df = gpd.read_file(gpkg_path, layer='dynamische_autoparkeerdata')

    df['Starttime'] = pd.to_datetime(df['Starttime'], format = "%Y-%m-%d %H:%M:%S")

    df['Starttime'] = df['Starttime'].dt.floor('5min')
    df = df.groupby(['ID', 'Starttime']).first().reset_index()

    # parkeerduur en bezetting toevoegen
    # is lokale tijd
    df['Capacity'] = df['Capacity'].astype(int)
    df['Vacantspaces'] = df['Vacantspaces'].astype(int)
    
    # gaten vullen
    all_data = []

    # Vul ontbrekende tijdstippen voor elke ID
    for id_ in df['ID'].unique():
        # Haal de data voor de huidige ID op
        id_data = df[df['ID'] == id_]

        # Haal het minimale en maximale tijdstip per ID
        start_time_min = id_data['Starttime'].min()
        end_time_max = id_data['Starttime'].max()
        
        # Maak een tijdreeks van 5 minuten voor de huidige ID
        id_times = pd.date_range(start=start_time_min, end=end_time_max, freq='5min')
        
        # Maak een nieuwe DataFrame met deze tijdstippen
        filled_data = pd.DataFrame({'Starttime': id_times})
        filled_data['ID'] = id_

        # Merge met de originele data om de waarden op te vullen
        filled_data = pd.merge(filled_data, id_data, on=['Starttime', 'ID'], how='left')

        # Voeg het ingevulde DataFrame toe aan de lijst
        all_data.append(filled_data)
    
    # Combineer de gegevens van alle IDs
    df = pd.concat(all_data)

    # vul de ontbrekende waarden op
    all_data_filled = []

    # Vul ontbrekende waarden per ID per dag
    for id_ in df['ID'].unique():
        id_data = df[df['ID'] == id_]
        
        for datum in id_data['Starttime'].dt.date.unique():
            daily_data = id_data[id_data['Starttime'].dt.date == datum]
            
            # Vul de ontbrekende waarden per dag met forward fill
            data_without_id_time = daily_data.drop(columns=['ID', 'Starttime'])
            data_without_id_time = data_without_id_time.ffill()
            
            # Voeg de ingevulde data weer samen
            filled_daily_data = daily_data[['ID', 'Starttime']].join(data_without_id_time)
            
            # Voeg de ingevulde gegevens toe aan de lijst
            all_data_filled.append(filled_daily_data)

    # Combineer de ingevulde gegevens van alle IDs
    df = pd.concat(all_data_filled)
    df = df[~df['Full'].isna()]
    
    df['datum'] = df['Starttime'].dt.date
    df['dag'] = df['datum'].astype(str) 
    lijstdag = df['dag'].unique()
    lijstid = df['ID'].unique()
    df['datum'] = df['datum'].astype(str) 

    df['Occupancy'] = df['Capacity'] - df['Vacantspaces']
    df.loc[df['Occupancy'] < 0, 'Occupancy'] = 0
    
    
    df = df.sort_values(by = ['ID', 'Starttime'], ignore_index = True)
    
    name, datei, opp = [],[],[]

    
    for ide in lijstid:
    
        for datum in lijstdag:
            
            try:
                df1 = df[(df['datum'] == datum) & (df['ID'] == ide)]
                
                y = df1['Occupancy'].values
                
                # oppervlakte onder het diagram            
                # Compute the area using the composite trapezoidal rule.
                area1 = trapz(y, dx=1)
                # Compute the area using the composite Simpson's rule.
                area2 = simpson(y, dx=1)
                area = int(0.5 * (area1 + area2))
            except:
                area = 0
            
            name.append(ide)
            datei.append(datum)
            opp.append(area)
    
    zippy = zip(name,datei,opp)
    dfagg = pd.DataFrame(data = zippy, columns = ['ID', 'datum', 'Duration'])


    df = pd.merge(df, dfagg, how = 'left', on = ['ID', 'datum'])

    df['Starttime'] = df['Starttime'].astype(str)

    df = df.drop(columns = ['dag', 'datum'])
    
    df[['Open', 'Full', 'LastUpdated', 'Capacity', 'Vacantspaces', 'Occupancy', 'Duration']] = df[['Open', 'Full', 'LastUpdated', 'Capacity', 'Vacantspaces', 'Occupancy', 'Duration']].astype(int)
    
    #gpd.GeoDataFrame(df).to_file(gpkg_path, layer = "dynamische_autoparkeerdata2", driver="GPKG", mode = 'w')
    verw = gpd.GeoDataFrame(df)
    return verw

#verwerken_dynamische_data()
#print(ka)


def call_api_every_3_minutes():
    while True:
        # Voer de bevraging om de 3 minuten uit
        nonstop = nonstop_dynamische_data()
        write_queue.put((nonstop, "dynamische_autoparkeerdata"))

        time.sleep(3 * 60)  # Wacht 3 minuten
        
        verw = verwerken_dynamische_data()
        write_queue.put((verw, "dynamische_autoparkeerdata2"))
        
        time.sleep(1)


# Functie voor het schrijven naar de GeoPackage
def write_to_geopackage(gpkg_path):
    while True:
        # Haal data en layer_name uit de queue
        data, layer_name = write_queue.get()
        
        with db_lock:  # Vergrendel de database voor andere threads
            if isinstance(data, gpd.GeoDataFrame):  # Zorg ervoor dat het een GeoDataFrame is
                # Schrijf de data naar de GeoPackage in de opgegeven laag
                mode = 'a'
                if layer_name == 'dynamische_autoparkeerdata2': mode = 'w'
                data.to_file(gpkg_path, layer=layer_name, driver="GPKG", mode= mode)  # 'a' voor append
                #print(f"Data toegevoegd aan laag: {layer_name}")
            write_queue.task_done()

def start_threads():
    # Start de thread voor de wekelijkse bevraging
    weekly_thread = threading.Thread(target=call_api_weekly, daemon=True)
    weekly_thread.start()

    # Start de thread voor de 3-minuten bevraging
    three_minute_thread = threading.Thread(target=call_api_every_3_minutes, daemon=True)
    three_minute_thread.start()

    # Start de thread voor het schrijven naar de GeoPackage
    writer_thread = threading.Thread(target=lambda: write_to_geopackage(gpkg_path), daemon=True)
    writer_thread.start()

start_threads()


st.markdown(
    """
    <style>
    .main {
        background-color: #A9A9A9; /* Lichtblauw */
    }
    .stButton > button {
        width: 100%;
        height: 80px; /* Hoogte van de knoppen */
        font-size: 48px; /* Tekstgrootte van de knoppen */
        line-height: 10px; /* Decrease line spacing */
        border: none;
        color: white;
        text-align: center;
        text-decoration: none;
        display: inline-block;
        margin: 0px 0px;
        cursor: pointer;
    }
    .stRow {
        display: flex;
        flex-wrap: wrap;
        justify-content: center;
    }
    .stColumn {
        flex: 0 0 30%; /* Verander deze waarde om de breedte van de kolommen aan te passen */
        gap: 0rem;
        padding: 10px; /* Ruimte tussen de kolommen */
    }
    }
    .po0 {
        width: 300px; /* Hoogte van de knoppen */

        }
    .po {
        width: 300px; /* Hoogte van de knoppen */

        }
    .po2 {
        width: 300px; /* Hoogte van de knoppen */

        }

    </style>
    """,
    unsafe_allow_html=True
)

st.markdown("""
<style>
    [data-testid=stSidebar] {
        background-color: #d3d3d3;
        min-width: 200px !important;  /* Pas de minimale breedte aan */
        max-width: 200px !important;  /* Pas de maximale breedte aan */
    }
</style>
""", unsafe_allow_html=True)


st.markdown(
        """
        <style>
            [data-testid="stSidebarNav"] {
                background-color: #FFFFFF !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

# Logo toevoegen in de sidebar
logo_path = "footerlogo-768x143.png"  # Vervang dit met het pad naar je logo
st.sidebar.image(logo_path)
st.sidebar.title('')
st.sidebar.markdown("<h1 style='text-align: center; font-size: 30px; color: white;'>Dashboard parkeerdata</h1>", unsafe_allow_html=True)
st.sidebar.markdown("<h2 style='text-align: center; font-size: 18px; color: white;'>Autoparkeren", unsafe_allow_html=True)

if "screen" not in st.session_state:
    st.session_state.screen = "Autoparkeren"

# Function to get user input
def get_user_input(po):


    with po:
        
        st.markdown(
            """
            <style>
                /* Adjust row height for sliders */
                .stSlider>div>div {
                    height: 10px;
                }
                /* Adjust row height for date input */
                .stDateInput>div>div {
                    height: 40px;
                }
                /* Adjust row height for text input */
                .stNumberInput>div>div {
                    height: 20px;
                    widht: 30 px;
                }
                /* Adjust row height for dropdown input */
                .stSelectbox>div>div {
                    height: 40px;
                    widht: 60 px;
    
                }
    
            </style>
            """, 
            unsafe_allow_html=True
        )

    
        date_range = date_range_picker("Datumreeks")
    
        # Split day names into four groups
        groups = [['Ma', 'Vr'], ['Di', 'Za'], ['Wo', 'Zo'], ['Do']]
        
        # Create four columns in the sidebar
        cols = st.columns(4)
        
        selected_days = []
                            
        for i, group in enumerate(groups):
            for day_index, day in enumerate(group):
                key = f"{day}_{day_index}"  # Unique key for each checkbox
                selected = cols[i].checkbox(day, value=True, key = key)
                if selected:
                    selected_days.append(day)
        
        dagperiode = st.selectbox('Dagperiode', ('0-24 uur','0-6 uur', '6-10 uur', '10-15 uur', '15-19 uur', '19-24 uur','7-9 uur','16-18 uur'))
        
        #time_range = st.sidebar.slider('Dagperiode', 0,24, (7, 9), step=1, key ='1')
    
    
        # referentiesituatie
        date_range_R = date_range_picker("Datumreeks referentie")

        
        # Create four columns in the sidebar
        cols = st.columns(4)
        
        selected_days_R = []
                            
        for i, group in enumerate(groups):
            for day_index, day in enumerate(group):
                key = f"{day}_{day_index}_R"  # Unique key for each checkbox
                selected2 = cols[i].checkbox(day, value=True, key = key)
                if selected2:
                    selected_days_R.append(day)
    
        #time_range_R = st.sidebar.slider('Dagperiode referentie:', 0,24, (7, 9), step=1, key = '1_R')
        dagperiode_R = st.selectbox('Dagperiode referentie', ('0-24 uur','0-6 uur', '6-10 uur', '10-15 uur', '15-19 uur', '19-24 uur','7-9 uur','16-18 uur'))

    
        return date_range, selected_days, dagperiode, date_range_R, selected_days_R, dagperiode_R

def get_user_input2(po2):

    with po2:
        
        tijdseenheid = st.selectbox('**Tijdseenheid**  ', options = ['5-min','kwartier', 'uur', 'dag', 'week', 'maand', 'kwartiel', 'jaar'])
        
        colsel = ['Min_vrije_plaatsen', 'Vrije_plaatsen_15p', 'Bezetting_85p', 'Max_bezetting']
        if tijdseenheid != 'uur': colsel = ['Min_vrije_plaatsen', 'Vrije_plaatsen_15p', 'Bezetting_85p', 'Max_bezetting', 'Parkeerduur']
        
        indicator = st.selectbox('**Indicator**  ', options = colsel)
    
        yrefh = st.text_input('**Ref lijn**  ')
        try:
            yref = int(yrefh)
        except:
            yref = None

        
        ##with colu4:
        colu1, spacie, colu2 = st.columns([0.48,0.04,0.48])
        
        with colu1:
            colour1 = st.color_picker('**Kleur instellingen**  ', value = '#0000FF')
        
        with colu2:
            colour2 = st.color_picker('**Kleur referentie**  ', value = '#FF0000')
    
        return tijdseenheid, indicator, yref, colour1, colour2


def maken_selecties(datums,datumsR,dagperiode,dagperiodeR,dagsoorten,dagsoortenR, tijdseenheid):

   succes = 0    
   # Controleer eerst of gdf2 al beschikbaar is in session_state

   datums = pd.to_datetime(datums)
   datumsR = pd.to_datetime(datumsR)

   with db_lock:  # De lock wordt pas verkregen als gdf2 nog niet bestaat
         grafiekinputdata = gpd.read_file(gpkg_path, layer='dynamische_autoparkeerdata2')
         st.session_state['grafiekinputdata'] = grafiekinputdata  # Sla gdf2 op in session_state voor later gebruik
   if grafiekinputdata is None:
       grafiekinputdata = st.session_state.get('grafiekinputdata')
       
   data = grafiekinputdata  # Gebruik de geladen gdf2 in plaats van opnieuw inleiden

   
   if data is not None:
        succes = 1
        
       
        data['datum'] = pd.to_datetime(data['Starttime'], format = "%Y-%m-%d %H:%M:%S", errors='coerce')
        
        data['weekdag'] = data['datum'].dt.weekday
        data['uur'] = data['datum'].dt.hour.astype(int)
        
        dagperdict = {'0-24 uur': [0,24],'0-6 uur': [0,6], '6-10 uur': [6,10], '10-15 uur' : [10,15],'15-19 uur': [15,19], '19-24 uur': [19,24], '7-9 uur': [7,9], '16-18 uur': [16,18]}
       
        dagperiode = dagperdict[dagperiode]
        dagperiodeR = dagperdict[dagperiodeR]
        
        datumsdict = {'df1': datums, 'df2': datumsR}
        periodesdict = {'df1': dagperiode, 'df2': dagperiodeR}
        dagendict = {'df1': dagsoorten, 'df2': dagsoortenR}
        dagsoortdict = {'Ma' : 0, 'Di': 1, 'Wo': 2, 'Do': 3, 'Vr': 4, 'Za': 5, 'Zo': 6}
        reeksdict = {'df1': 'instellingen', 'df2': 'referentie'}
        
        # basissituatie
        frame = 'df1'
        dat = datumsdict[frame]
        per = periodesdict[frame]
        dag = dagendict[frame]

        df1 = data[(data['datum'] >= dat[0]) & (data['datum'] < dat[1])]

        df1 = df1[(df1['uur'] >= int(per[0])) & (df1['uur'] < int(per[1]))]
        df1['vlag'] = 0
        for dagje in dag:
            df1.loc[(df1['weekdag'] == dagsoortdict[dagje]), 'vlag'] = 1
        df1 = df1[df1['vlag'] == 1]
        df1 = df1.drop(columns = ['weekdag', 'vlag'])
        df1['datum'] = df1['datum'].astype(str)
        df1['reeks'] = reeksdict[frame]

        # refrentiesituatie
        frame = 'df2'
        dat = datumsdict[frame]
        per = periodesdict[frame]
        dag = dagendict[frame]
        df2 = data[(data['datum'] >= dat[0]) & (data['datum'] < dat[1])]
        df2 = df2[(df2['uur'] >= int(per[0])) & (df2['uur'] < int(per[1]))]
        df2['vlag'] = 0
        for dagje in dag:
            df2.loc[(df2['weekdag'] == dagsoortdict[dagje]), 'vlag'] = 1
        df2 = df2[df2['vlag'] == 1]
        df2 = df2.drop(columns = ['weekdag', 'vlag'])
        df2['datum'] = df2['datum'].astype(str)
        df2['reeks'] = reeksdict[frame]
        
        # maken indicatoren
        if df2.empty:
            df2 = df1.copy()

        data = pd.concat([df1,df2], axis=0, ignore_index = True)
        
        data['Starttime'] = pd.to_datetime(data['Starttime'],format = "%Y-%m-%d %H:%M:%S", errors='coerce')
        
        ids = st.session_state['selected_ids']
        if len(ids) != 0:
            datah = data[data['ID'].isin(ids)]
            if len(datah) != 0:
                data = datah
                
        publiccode = data['Name'].unique()
        if len(ids) == 0:
            titel = 'Alle parkeerfaciliteiten'
        elif len(ids) > 0 and len(ids) <=2:
            titel = ',<br>'.join(publiccode)
        else:
            titel = 'Meerdere parkeerfaciliteiten'
            
        if tijdseenheid == 'jaar': tijd = data['Starttime'].dt.year.astype(int)
        if tijdseenheid == 'uur': tijd = data['Starttime'].dt.hour.astype(int)
        if tijdseenheid == 'kwartier':
            kwartier = (data['Starttime'].dt.minute // 3) * 3
            tijd = data['Starttime'].dt.hour.astype(int) + kwartier / 60
        if tijdseenheid == '5-min': tijd = data['Starttime'].dt.floor('5min').dt.strftime('%H:%M')
        if tijdseenheid == 'dag': tijd = data['Starttime'].dt.date
        if tijdseenheid == 'week': 
            tijd = (data['Starttime'].dt.year.astype(str) + 
                data['Starttime'].dt.isocalendar().week.map("{:02}".format).astype(str)).astype(int)
            
            #tijd = (data['Starttime'].dt.year.astype(str) + data['Starttime'].dt.isocalendar().week.map("{:02}".format).astype(str)).astype(int)
        if tijdseenheid == 'maand': tijd = (data['Starttime'].dt.year.astype(str) + data['Starttime'].dt.month.map("{:02}".format).astype(str)).astype(int)
        if tijdseenheid == 'kwartiel': tijd = pd.PeriodIndex(data['Starttime'].dt.date, freq='Q').astype(str)
    
        
        data = data.groupby(['reeks', 'ID', tijd]).agg(Capaciteit = ('Capacity','mean'), \
            Min_vrije_plaatsen = ('Vacantspaces','min'), Vrije_plaatsen_15p= ('Vacantspaces', percentile(15)), \
            Parkeerduur= ('Duration', 'mean'), Max_bezetting = ('Occupancy', 'max'),Bezetting_85p= ('Occupancy', percentile(85))).reset_index()
            
        if tijdseenheid == 'week':
            data.rename(columns={'level_2': 'Starttime'}, inplace = True)
    
        data = data.groupby(['reeks', 'Starttime']).agg({'Capaciteit': 'sum', 'Min_vrije_plaatsen': 'sum', \
                    'Vrije_plaatsen_15p': 'sum', 'Bezetting_85p': 'sum', 'Max_bezetting': 'sum', 'Parkeerduur': 'mean'}).reset_index()
        
        data['Starttime'] = data['Starttime'].astype(str)
    
   else:
        data = []
        titel = ''
         
   del df1, df2     
   return data, titel, succes



def visualiseren():
    
        col1, col2, col3 = st.columns([0.33,0.33,0.33])

        with col1:

            with stylable_container(
                    key="popoverbutton",
                    css_styles="""
                        button {
                            width: 150px;
                            height: 60px;
                            background-color: lightblue;
                            color: white;
                            border-radius: 40px;
                            margin-left: 40px;
                            white-space: nowrap;
                        }
                        """,
                ):
                
                    po = st.popover(label='Selecteren data', use_container_width=True)

        with col2:
            
            if st.session_state['actueel'] == 1:
                button_color = "background-color: #4CAF50; color: white;"  
            else:
                button_color = "background-color: #FF6347; color: white;"


            with stylable_container(
                    key="popoverbutton0",
                    css_styles=f"""
                        button {{
                            width: 150px;
                            height: 60px;
                            {button_color}
                            color: white;
                            border-radius: 40px;
                            margin-left: 40px;
                            white-space: nowrap;
                        }}
                        """,
                ):
                
                    po0 = st.button(label='Actueel', use_container_width=True)
                    if po0:
                        if st.session_state['actueel'] == 1:
                            st.session_state['actueel'] =0  
                        else:
                            st.session_state['actueel'] = 1
                        st.rerun()    

        with col3:

            with stylable_container(
                    key="popoverbutton2",
                    css_styles="""
                        button {
                            width: 150px;
                            height: 60px;
                            background-color: lightblue;
                            color: white;
                            border-radius: 40px;
                            margin-left: 40px;
                            white-space: nowrap;
                        }
                        """,
                ):
                
                    po2 = st.popover(label='Grafiekinstellingen', use_container_width=True)
        
        space1,col1, space2, col2, space3 = st.columns([0.01,0.485,0.01,0.485, 0.01])


        with col1:
            
            
            # Initialize session state if not already done
            if 'zoom' not in st.session_state:
                st.session_state['zoom'] = 14  # Standaard zoomniveau
            if 'center' not in st.session_state:
                st.session_state['center'] = [52.207, 5.977]  # Standaard centerpositie, bijvoorbeeld in Apeldoorn
            if 'selected_ids' not in st.session_state:
                st.session_state['selected_ids'] = []
            if 'last_clicked' not in st.session_state:
                st.session_state['last_clicked'] = None
            if 'selected_mutaties' not in st.session_state:
                st.session_state['selected_mutaties'] = 0
            #if 'data_loaded' not in st.session_state:
            #    st.session_state['data_loaded'] = False  # Voeg de 'data_loaded' status toe

            def get_color(vacantspaces):
                colormap = branca.colormap.linear.RdYlGn_09.scale(0, 200)
                colormap.caption = 'Vacant Spaces'
                return colormap(vacantspaces)  # Verkrijg de kleur van de colormap op basis van vacantspaces

            #st.session_state.data_updated = False

            def create_map():
                
                
                m = folium.Map(location=st.session_state['center'], zoom_start=st.session_state['zoom'], tiles=None)
                
                with db_lock:
                #try:    
                    kaartdata = gpd.read_file(gpkg_path, layer = 'statische_autoparkeerdata')
                    kaartdata = kaartdata.set_crs(4326)
                    vac = gpd.read_file(gpkg_path, layer = 'dynamische_autoparkeerdata2')
                    st.session_state['grafiekinputdata'] = vac
                    st.session_state['kaartdata'] = kaartdata
                #except:
                #    pass


                if kaartdata is None:
                    kaartdata = st.session_state.get('kaartdata')
                    vac = st.session_state.get('grafiekinputdata')


                if kaartdata is not None:

                    vac['Starttime'] = pd.to_datetime(vac['Starttime'])  # Zet de tijd om naar datetime
                    vac_recent = vac.sort_values('Starttime', ascending=False).drop_duplicates('ID', keep='first')
                    kaartdata = kaartdata.merge(vac_recent[['ID', 'Vacantspaces']], on='ID', how='left')
                    kaartdata['Vacantspaces'] = kaartdata['Vacantspaces'].fillna(-1).astype(int)
                    
                    # Sla de data op in session_state zodat het later beschikbaar is
                    st.session_state['kaartdata'] = kaartdata
                    #st.session_state['data_loaded'] = False  # Zet de sessie-status op True

                    # bij gebruik rechtsreeks met Leaflet is een API key noodzakelijk
                    Stadia_AlidadeSmoothDark = 'https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png?api_key=83fd135a-4500-4d47-9b73-ac8757d5eeed'
                    attr= "<a href=https://stadiamaps.com/>Stadia Maps</a>"
    
                    #m = folium.Map(tiles = None)
                    #m.fit_bounds(bounds)
                    #m = folium.Map(location=st.session_state['center'], zoom_start=st.session_state['zoom'], tiles=None)
                    folium.TileLayer(Stadia_AlidadeSmoothDark, attr = attr, name = "Stadia").add_to(m)
                                #m.add_child(fg)
                                
                    # Add the legend to the map
                    
                    macro = MacroElement()
                    macro._template = Template(legend_template)
                    m.get_root().add_child(macro)            

                    #fg1 = folium.FeatureGroup(name='Ov lijnvoering', show=True)
                    folium.GeoJson(kaartdata[['geometry', 'ID','Name', 'Vacantspaces']],
                          style_function=lambda x: {'fillColor': get_color(x['properties']['Vacantspaces']), 'color': 'black', 'weight': 1.0, 'fillOpacity': 1.0},
    
                          tooltip =  folium.features.GeoJsonTooltip(fields=['Name', 'Vacantspaces'],
                                                                    aliases=['Name', 'Vacant spaces'],
                                                                    labels=True, sticky=True, localize=True,
                                                                    style=("font-size: 20px; " ))
    
                                  ).add_to(m)
                        
                        
                        #st.session_state['data_loaded'] = True  # Zet de sessie-status op True
                    
                else:
                    #minx, miny, maxx, maxy = gdf2.total_bounds
                    #bounds = [[miny, minx], [maxy, maxx]]
                    #bounds = [[52.20744,5.95230], [52.22398,5.97937]]  # voor Apeldoorn
                    folium.Marker(location=st.session_state['center'], popup="Gegevens kunnen niet worden geladen.").add_to(m)
                    
                    #st.session_state['data_loaded'] = False
                
                return  m

            
            def toevoegen_fg():
                
                fg = folium.FeatureGroup(name="Selection")
                
                if 'kaart' in st.session_state and len(st.session_state['selected_ids']) > 0:
                    kaart = st.session_state.get('kaart')
                    sel = st.session_state["selected_ids"]
                    gdfh = kaart[kaart['ID'].isin(sel)]

                    if len(gdfh) > 0:
                        color = 'purple'
                        folium.GeoJson(gdfh[['geometry', 'ID','Name']],
                              style_function=lambda x, color=color: {'fillColor': 'purple', 'color': 'purple', 'weight': 1.0, 'fillOpacity': 1.0},
                              tooltip =  folium.features.GeoJsonTooltip(fields=['Name',], labels=True, sticky=True, 
                                                                        style=("font-size: 20px; " ))
                                ).add_to(fg)
                
                return fg


            m = create_map()
            fg = toevoegen_fg()
            click_result = st_folium(m,center=st.session_state["center"], zoom=st.session_state["zoom"], feature_group_to_add=fg, width=800, height=750)
            fg = folium.FeatureGroup(name="Selection")
            
            #st.session_state.data_updated = False
              
            if click_result['last_object_clicked'] is not None and click_result['last_clicked'] is not None:
                
                if click_result['last_clicked'] != st.session_state['last_clicked']:
                        if click_result['last_object_clicked']['lat']:
                            lat = click_result['last_object_clicked']['lat']
                            lon = click_result['last_object_clicked']['lng']
                            
                            center = [lat,lon]
                            zoom = click_result['zoom']
                            clicked_point = gpd.GeoSeries([gpd.points_from_xy([lon], [lat])[0]], crs="EPSG:4326")
                            kaart = st.session_state.get('kaart')
                            selected_polygon = kaart[kaart.geometry.contains(clicked_point.geometry[0])]
                            
                            
                            if not selected_polygon.empty:
                                line_id = selected_polygon.iloc[0]['ID']
                                if line_id in st.session_state['selected_ids']:
                                      st.session_state['selected_ids'].remove(line_id)
                                else:
                                      st.session_state['selected_ids'].append(line_id)
                                      #st.session_state['selected_id'] = selected_polygon.iloc[0]['line_id']
                                #click_result['last_object_clicked'] = None
                                
                                
                                st.session_state['zoom'] = zoom
                                st.session_state['center'] = center
                                st.session_state['last_clicked'] = click_result['last_clicked']
                                st.session_state['selected_mutaties'] =1
                                
                                #print('BELANG', st.session_state['selected_ids'])
                                
                                #st.session_state.data_updated = True
        
                                st.rerun()

        with col2:
            
            
            if st.session_state['actueel'] ==1:
                tijdseenheid = '5-min'
                indicator = 'Min_vrije_plaatsen'
                yref = None
                colour1 = '#0000FF'
                colour2 = '#FF0000'
                dagen_nl = {'Mon': 'Ma', 'Tue': 'Di', 'Wed': 'Wo', 'Thu': 'Do', 'Fri': 'Vr', 'Sat': 'Za', 'Sun': 'Zo'}
                datum1 = pd.to_datetime(pd.Timestamp.now().normalize())
                datum2 = datum1 + pd.Timedelta(days=1)
                datum1_date = datum1.date()
                datum2_date = datum2.date()
                datums = (datum1_date,datum2_date)
                dagsoort = datum1.strftime('%a')  # Dit geeft een Engelse afkorting zoals 'Mon', 'Tue', etc.
                dag_nl = dagen_nl[dagsoort]
                dagsoorten = [dag_nl]
                dagperiode = '0-24 uur'
                # als refentie nemen we gisteren
                datum0 = datum1 - pd.Timedelta(days=1)
                datum0_date = datum0.date()
                datumsR = (datum0_date,datum1_date)
                dagsoort = datum0.strftime('%a')  # Dit geeft een Engelse afkorting zoals 'Mon', 'Tue', etc.
                dag_nl = dagen_nl[dagsoort]
                dagsoortenR = [dag_nl]
                dagperiodeR = '0-24 uur'
                del dagen_nl, datum1, datum2, datum1_date, datum2_date,dagsoort,dag_nl,datum0,datum0_date
                # Sla de eerste keer data op in session_state
            else:
                tijdseenheid, indicator, yref, colour1, colour2 = get_user_input2(po2)
                datums, dagsoorten, dagperiode, datumsR, dagsoortenR, dagperiodeR = get_user_input(po)

            if not st.session_state.has_run:
                st.session_state.invoer = [datums,datumsR,dagsoorten,dagsoortenR,dagperiode,dagperiodeR, tijdseenheid]
                grafiekdata, titel, succes = maken_selecties(datums,datumsR,dagperiode,dagperiodeR,dagsoorten,dagsoortenR, tijdseenheid)
                st.session_state.has_run = True
                if succes == 1:
                    st.session_state.grafiekdata = grafiekdata
                    st.session_state.titel = titel
            else:
                invoer = [datums,datumsR,dagsoorten,dagsoortenR,dagperiode,dagperiodeR, tijdseenheid]
                
                if invoer != st.session_state.invoer or st.session_state['selected_mutaties'] ==1:
                    grafiekdata, titel, succes = maken_selecties(datums,datumsR,dagperiode,dagperiodeR,dagsoorten,dagsoortenR, tijdseenheid)
                    if succes == 1:
                        st.session_state.grafiekdata = grafiekdata
                        st.session_state.titel = titel
                        st.session_state.invoer = invoer
                        st.session_state['selected_mutaties'] = 0
                    
            
            grafiekdata = st.session_state.grafiekdata
            titel = st.session_state.titel
            
            df1 = grafiekdata[grafiekdata['reeks'] == 'instellingen']
            df2 = grafiekdata[grafiekdata['reeks'] == 'referentie']
            
            # Als beide dataframes leeg zijn, maak dan een lege grafiek
            if df1.empty or df2.empty:
                fig = go.Figure()  # Maak een lege figuur
                fig.add_annotation(
                    text="Geen data beschikbaar voor de grafiek.",
                    xref="paper", yref="paper",
                    x=0.5, y=0.5, showarrow=False,
                    font=dict(size=20),
                    align="center",
                    bordercolor="black",
                    borderwidth=0,
                    borderpad=4,
                    bgcolor="white",
                    opacity=1
                )
            else:
            
                # Maak de figuur en voeg de lijnen toe
                fig = go.Figure()
                
                # Eerste lijngrafiek met twee lijnen
                
                fig.add_trace(go.Scatter(x=df2['Starttime'], y=df2[indicator], mode='lines', name='referentie', line=dict(color=colour2, width = 6)))
                fig.add_trace(go.Scatter(x=df1['Starttime'], y=df1[indicator], mode='lines', name='instellingen', line=dict(color=colour1, width=6)))
                
                if indicator == 'Max_bezetting' or indicator == 'Bezetting_85p':
                    fig.add_trace(go.Scatter(x=df1['Starttime'], y=df1['Capaciteit'], mode='lines', name='capaciteit', line=dict(color='purple', width = 6)))
    
                if yref != None:
                    fig.add_hline(y=yref, line_width=6, line_dash="dash", line_color="green")
            
            # https://stackoverflow.com/questions/70916649/how-to-change-the-x-axis-and-y-axis-labels-in-plotly
            
                # Voeg een titel toe met een wit kader
                fig.add_annotation(
                    text= 'Bezetting parkeerfaciliteiten <br>' + titel,
                    xref="paper", yref="paper",
                    x=0.5, y=1.18, showarrow=False,
                    font=dict(size=20),
                    align="center",
                    bordercolor="black",
                    borderwidth=0,
                    borderpad=4,
                    bgcolor="white",
                    opacity=1
                )
            
            fig.update_layout(
                autosize=False,
                width=800,
                height=535,
                plot_bgcolor='grey',
                minreducedheight = 300,
                xaxis_title=dict(text=tijdseenheid, font=dict(size=20, color='#000000')),
                yaxis_title=dict(text=indicator, font=dict(size=20, color='#000000')),
                #yaxis_range=[-10,5],
                xaxis_type='category',
                #xaxis=dict(tickfont=dict(size=10, color='#000000'),range=[df1['Starttime'].min(), df1['Starttime'].max()]),  # Zet het bereik van de x-as op de min en max van df1['Starttime']),
                xaxis=dict(tickfont=dict(size=10, color='#000000')),  # Zet het bereik van de x-as op de min en max van df1['Starttime']),
                yaxis=dict(tickfont=dict(size=10, color='#000000'),title_standoff=5),
                legend=dict(x=0.5, y=-0.3, orientation='h', font=dict(size=10, color='#000000'), xanchor = 'center', yanchor = 'top'),
                legend_title_text=''
            )
            
            # Grafiek weergeven in de Streamlit app
            st.plotly_chart(fig, use_container_width=True, theme = None)

            m = ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"]
            dag = sorted(dagsoorten, key=m.index)
            dagje = ', '.join(dag)
            dagR = sorted(dagsoortenR, key=m.index)
            dagjeR = ', '.join(dagR)

            with stylable_container(
                    key="container_with_border",
                    css_styles="""
                        {
                            border: 1px solid rgba(49, 51, 63, 0.2);
                            border-radius: 0.5rem;
                            background-color: white;
                            padding: calc(1em - 1px);
                            height: 300px;
                        }
                        """,
                ):
                    col3, col4 = st.columns([0.5,0.5])
                    
                    
                    with col3:
                        st.write("""
                                **Instellingen**  
                                Datumreeks: """ + str(datums[0])[:10] + """  tot  """ + str(datums[1])[:10] + """,  """
                                """Dagsoorten: """ + dagje + """,  """ + """<br>"""
                                """Dagperiode: """ + str(dagperiode),
                                unsafe_allow_html=True)
           
                    with col4:
                        st.write("""
                                **Referentie**  
                                Datumreeks: """ + str(datumsR[0])[:10] + """  tot  """ + str(datumsR[1])[:10] + """,  """
                                """Dagsoorten: """ + dagjeR + """,  """ + """<br>"""
                                """Dagperiode: """ + str(dagperiodeR),
                                unsafe_allow_html=True)
            #st.session_state.data_updated = False
            if not st.session_state.has_run:
                st.session_state.has_run = True
                #st.rerun()

            if st.session_state['auto_refresh'] and st.session_state['actueel'] == 1:
                st.session_state.has_run = False
                time.sleep(300)  # Refresh every 10 seconds
                st.rerun()


def main():
    
    st.markdown("""
    <style>
        .main > div {
            padding-top: 0rem;
            padding-left: 0rem;
            padding-right: 0rem;
        }
    </style>
    """, unsafe_allow_html=True)

    visualiseren()
    
    
if __name__ == "__main__":
    

    main()

