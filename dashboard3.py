from flask import Flask, jsonify, render_template
from flask_cors import CORS

from datetime import datetime
from decimal import Decimal

import mysql.connector
from mysql.connector import Error

from sshtunnel import SSHTunnelForwarder

# Local config (must exist)
from config.constants import (
    SSH_HOST,
    SSH_PORT,
    SSH_USER,
    SSH_PASS,
    DB_USER,
    DB_PASS,
    DB_NAME,
)


app = Flask(__name__)
CORS(app)


def get_db_connection():
    server = SSHTunnelForwarder(
        ssh_address_or_host=(SSH_HOST,SSH_PORT ), 
        ssh_username=SSH_USER,                   
        ssh_password=SSH_PASS,                
        remote_bind_address=('localhost', 3306)     
    )

    server.start()    
    conn=  mysql.connector.connect(
        host='localhost',              
        port=server.local_bind_port,   
        user=DB_USER,                
        password=DB_PASS,        
        database= DB_NAME        
    )
    return conn


@app.route("/")
def home():
    return render_template("dashboard3.html")


@app.route("/api/tableau-opioid-data")
def tableau_data():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT 
            e.Encounter_id,
            e.Age_in_years,
            e.Gender,
            e.Race,
            e.Caresetting_desc as Department,
            e.Dischg_disp_code_desc as Discharge_Status,
            m.GENERIC_NAME as Medication_Name,
            m.Order_strength as Dosage,
            m.MED_STARTED_DT_TM as Med_Start_Time,
            d.diagnosis_code as Diagnosis_Code,
            d.Diagnosis_description as Diagnosis_Desc,
            
            CASE 
                WHEN m.GENERIC_NAME REGEXP 'Morphine|Fentanyl|Oxycodone|Hydrocodone|Methadone|Tramadol|Hydromorphone' THEN 1 
                ELSE 0 
            END as Is_Opioid,

            CASE 
                WHEN m.GENERIC_NAME LIKE '%Naloxone%' THEN 1 
                ELSE 0 
            END as Is_Naloxone,

            CASE 
                WHEN d.diagnosis_code LIKE '965%' THEN 1 
                ELSE 0 
            END as Is_Overdose

        FROM hf_encounter e
        LEFT JOIN hf_medication m ON e.Encounter_id = m.Encounter_id
        LEFT JOIN hf_diagnosis d ON e.Encounter_id = d.Encounter_id
    """

    try:
        cursor.execute(query)
        rows = cursor.fetchall()
        
        for row in rows:
            val = row['Med_Start_Time']
            if val and not isinstance(val, str):
                row['Med_Start_Time'] = val.strftime('%Y-%m-%d %H:%M:%S')
        
        return jsonify(rows)

    except Exception as e:
        return jsonify({"error": str(e)})
    
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__': 
    app.run(debug=True, host='0.0.0.0', port=5000)
