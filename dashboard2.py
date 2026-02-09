from flask import Flask, jsonify, render_template
from flask_cors import CORS


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


@app.route('/')
def index():
    return render_template('dashboard2.html')

MME_FACTORS = {
    'TRAMADOL': 0.1,
    'CODEINE': 0.15,
    'HYDROCODONE': 1.0,
    'OXYCODONE': 1.5,
    'MORPHINE': 1.0,
    'HYDROMORPHONE': 4.0,
    'OXYMORPHONE': 3.0,
    'FENTANYL': 2.4,
    'METHADONE': 8.0,
    'BUPRENORPHINE': 30.0,
    'TAPENTADOL': 0.4
}


def get_mme_factor(medication_name):
    if not medication_name:
        return 0
    med_upper = medication_name.upper()
    for key, factor in MME_FACTORS.items():
        if key in med_upper:
            return factor
    return 0


def calculate_daily_mme(strength, frequency_desc, generic_name):
    try:
        if '-' in str(strength):
            dose_mg = float(strength.split('-')[0].replace('MG', '').strip())
        else:
            dose_mg = float(str(strength).replace('MG', '').strip())
    except:
        return 0.0
    
    conversion_factor = get_mme_factor(generic_name)
    if conversion_factor == 0:
        return 0.0
    
    freq_upper = str(frequency_desc).upper() if frequency_desc else ''
    
    if 'Q6H' in freq_upper or 'QID' in freq_upper:
        doses_per_day = 4
    elif 'Q8H' in freq_upper or 'TID' in freq_upper:
        doses_per_day = 3
    elif 'Q12H' in freq_upper or 'BID' in freq_upper:
        doses_per_day = 2
    elif 'Q24H' in freq_upper or 'QD' in freq_upper or 'DAILY' in freq_upper:
        doses_per_day = 1
    elif 'PRN' in freq_upper:
        doses_per_day = 4
    else:
        doses_per_day = 3
    
    daily_mme = dose_mg * conversion_factor * doses_per_day
    return round(daily_mme, 2)


@app.route('/tableau-data/<int:patient_id>')
def get_tableau_data(patient_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    query = """
    SELECT
        p.patient_id,
        p.race,
        p.gender,
        p.marital_status,
        e.encounter_id,
        e.admitted_dt_tm as encounter_date,
        e.age_in_years,
        e.payer_code_desc as insurance,
        m.medication_row_id,
        m.generic_name,
        m.order_strength,
        m.frequency_desc,
        m.med_started_dt_tm,
        m.med_stopped_dt_tm,
        mme.mme_score as stored_mme,
        p_od.label as od_risk_flag,
        p_oud.label as oud_risk_flag,
        d.diagnosis_code,
        d.diagnosis_description,
        (SELECT MAX(result_value_num)
         FROM hf_clinical_event ce
         WHERE ce.encounter_id = e.encounter_id
         AND ce.event_code_desc LIKE '%%Pain Score%%') as pain_score
    FROM t_patient p
    INNER JOIN hf_encounter e ON p.patient_id = e.patient_id
    INNER JOIN hf_medication m ON e.encounter_id = m.encounter_id
    LEFT JOIN t_MME mme ON e.encounter_id = mme.encounter_id
    LEFT JOIN t_prediction_od p_od ON e.patient_id = p_od.patient_id
    LEFT JOIN t_prediction_oud p_oud ON e.patient_id = p_oud.patient_id
    LEFT JOIN hf_diagnosis d ON e.encounter_id = d.encounter_id AND d.diagnosis_priority = 1
    WHERE p.patient_id = %s
    AND m.generic_name IS NOT NULL
    AND m.generic_name REGEXP 'TRAMADOL|CODEINE|HYDROCODONE|OXYCODONE|MORPHINE|FENTANYL|HYDROMORPHONE|METHADONE'
    ORDER BY m.med_started_dt_tm
    """
    
    try:
        cursor.execute(query, (patient_id,))
        data = cursor.fetchall()
        
        if not data:
            return jsonify({
                "error": f"No opioid prescription data found for patient {patient_id}"
            }), 404
        
        for row in data:
            if row.get('stored_mme') and row['stored_mme'] > 0:
                daily_mme = float(row['stored_mme'])
            else:
                daily_mme = calculate_daily_mme(
                    row['order_strength'],
                    row['frequency_desc'],
                    row['generic_name']
                )
            
            row['daily_mme'] = daily_mme
            
            if daily_mme >= 90:
                row['mme_category'] = 'Critical (≥90)'
                row['mme_risk_level'] = 4
            elif daily_mme >= 50:
                row['mme_category'] = 'High (50-89)'
                row['mme_risk_level'] = 3
            elif daily_mme >= 30:
                row['mme_category'] = 'Moderate (30-49)'
                row['mme_risk_level'] = 2
            else:
                row['mme_category'] = 'Low (<30)'
                row['mme_risk_level'] = 1
            
            med_name = row['generic_name'].upper()
            if 'TRAMADOL' in med_name:
                row['medication_class'] = 'Tramadol'
                row['potency'] = 'Low'
                row['potency_level'] = 1
            elif 'CODEINE' in med_name:
                row['medication_class'] = 'Codeine'
                row['potency'] = 'Low'
                row['potency_level'] = 1
            elif 'HYDROCODONE' in med_name:
                row['medication_class'] = 'Hydrocodone'
                row['potency'] = 'Moderate'
                row['potency_level'] = 2
            elif 'OXYCODONE' in med_name:
                row['medication_class'] = 'Oxycodone'
                row['potency'] = 'High'
                row['potency_level'] = 3
            elif 'MORPHINE' in med_name:
                row['medication_class'] = 'Morphine'
                row['potency'] = 'High'
                row['potency_level'] = 3
            elif 'FENTANYL' in med_name:
                row['medication_class'] = 'Fentanyl'
                row['potency'] = 'Very High'
                row['potency_level'] = 4
            elif 'METHADONE' in med_name:
                row['medication_class'] = 'Methadone'
                row['potency'] = 'Very High'
                row['potency_level'] = 4
            else:
                row['medication_class'] = 'Other Opioid'
                row['potency'] = 'High'
                row['potency_level'] = 3
            
            row['high_mme_flag'] = 1 if daily_mme >= 90 else 0
            row['moderate_mme_flag'] = 1 if daily_mme >= 50 else 0
            
            if row.get('encounter_date'):
                row['encounter_date'] = row['encounter_date'].isoformat()
            if row.get('med_started_dt_tm'):
                row['med_started_dt_tm'] = row['med_started_dt_tm'].isoformat()
            if row.get('med_stopped_dt_tm') and row['med_stopped_dt_tm']:
                row['med_stopped_dt_tm'] = row['med_stopped_dt_tm'].isoformat()
            
            row['pain_score'] = float(row['pain_score']) if row.get('pain_score') else 0.0
            row['od_risk_flag'] = int(row['od_risk_flag']) if row.get('od_risk_flag') else 0
            row['oud_risk_flag'] = int(row['oud_risk_flag']) if row.get('oud_risk_flag') else 0
            
            dx_code = row.get('diagnosis_code', '')
            row['mental_health_dx'] = 1 if dx_code and dx_code.startswith('F') else 0
            row['substance_abuse_dx'] = 1 if dx_code and dx_code.startswith('F1') else 0
        
        return jsonify(data)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/test')
def test_connection():
    """Test database connection"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT COUNT(*) as count FROM t_patient")
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'message': 'Database connected successfully',
            'patient_count': result['count']
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


if __name__ == '__main__': 
    app.run(debug=True, host='0.0.0.0', port=5000)

