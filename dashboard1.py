from flask import Flask, jsonify, render_template
from datetime import datetime
import mysql.connector
from mysql.connector import Error
from sshtunnel import SSHTunnelForwarder
from flask_cors import CORS
from decimal import Decimal

from config.constants import (
    SSH_HOST, SSH_PORT, SSH_USER, SSH_PASS,
    DB_USER, DB_PASS, DB_NAME
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

def query_database(query, params=None):
    """
    Execute database query with MySQL connection
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(query, params)
        results = cursor.fetchall()
        
        for row in results:
            for key, value in row.items():
                if isinstance(value, Decimal):
                    row[key] = float(value)
                elif isinstance(value, datetime):
                    row[key] = value.isoformat()
        
        return results
    except Exception as e:
        print(f"Database error: {e}")
        print(f"Query: {query}")
        print(f"Params: {params}")
        return []
    finally:
        cursor.close()
        conn.close()



@app.route('/api/diagnose/<int:patient_id>')
def diagnose_patient(patient_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        report = {
            'patient_id': patient_id,
            'raw_data': {},
            'field_analysis': {},
            'recommendations': []
        }
        
        cursor.execute(f"""
            SELECT * FROM hf_encounter 
            WHERE patient_id = %s 
            LIMIT 1
        """, (patient_id,))
        encounter_sample = cursor.fetchone()
        
        if encounter_sample:
            report['raw_data']['encounter_sample'] = encounter_sample
            report['field_analysis']['encounter_fields'] = list(encounter_sample.keys())
            
          
            null_fields = [k for k, v in encounter_sample.items() if v is None]
            report['field_analysis']['encounter_null_fields'] = null_fields
        else:
            report['recommendations'].append('⚠️ No encounters found for this patient')
        
      
        cursor.execute(f"""
            SELECT m.*, e.patient_id
            FROM hf_medication m
            JOIN hf_encounter e ON m.encounter_id = e.encounter_id
            WHERE e.patient_id = %s
            LIMIT 1
        """, (patient_id,))
        med_sample = cursor.fetchone()
        
        if med_sample:
            report['raw_data']['medication_sample'] = med_sample
            report['field_analysis']['medication_fields'] = list(med_sample.keys())
            null_fields = [k for k, v in med_sample.items() if v is None]
            report['field_analysis']['medication_null_fields'] = null_fields
        else:
            report['recommendations'].append('⚠️ No medications found for this patient')
        
       
        cursor.execute(f"""
            SELECT d.*, e.patient_id
            FROM hf_diagnosis d
            JOIN hf_encounter e ON d.encounter_id = e.encounter_id
            WHERE e.patient_id = %s
            LIMIT 1
        """, (patient_id,))
        diag_sample = cursor.fetchone()
        
        if diag_sample:
            report['raw_data']['diagnosis_sample'] = diag_sample
            report['field_analysis']['diagnosis_fields'] = list(diag_sample.keys())
            null_fields = [k for k, v in diag_sample.items() if v is None]
            report['field_analysis']['diagnosis_null_fields'] = null_fields
        else:
            report['recommendations'].append('No diagnoses found for this patient')
        
        
        cursor.execute(f"""
            SELECT DISTINCT m.generic_name
            FROM hf_medication m
            JOIN hf_encounter e ON m.encounter_id = e.encounter_id
            WHERE e.patient_id = %s
            LIMIT 20
        """, (patient_id,))
        med_names = cursor.fetchall()
        report['raw_data']['all_medication_names'] = [m['generic_name'] for m in med_names if m['generic_name']]
        
        opioid_patterns = ['oxycodone', 'hydrocodone', 'morphine', 'fentanyl', 'codeine', 'tramadol']
        matching_meds = []
        non_matching_meds = []
        
        for med in report['raw_data']['all_medication_names']:
            if med and any(pattern in med.lower() for pattern in opioid_patterns):
                matching_meds.append(med)
            else:
                non_matching_meds.append(med)
        
        report['field_analysis']['opioid_medications_found'] = len(matching_meds)
        report['field_analysis']['matching_medications'] = matching_meds
        report['field_analysis']['non_opioid_medications'] = non_matching_meds[:10]  
        if len(matching_meds) == 0:
            report['recommendations'].append('⚠️ No opioid medications found - medication names may not match search patterns')
        
        cursor.execute(f"""
            SELECT 
                COUNT(DISTINCT e.encounter_id) as total_encounters,
                COUNT(DISTINCT m.medication_row_id) as total_medications,
                COUNT(DISTINCT d.diagnosis_row_id) as total_diagnoses
            FROM hf_encounter e
            LEFT JOIN hf_medication m ON e.encounter_id = m.encounter_id
            LEFT JOIN hf_diagnosis d ON e.encounter_id = d.encounter_id
            WHERE e.patient_id = %s
        """, (patient_id,))
        
        counts = cursor.fetchone()
        report['field_analysis']['record_counts'] = counts
        
        cursor.close()
        conn.close()
        
        return jsonify(report)
        
    except Exception as e:
        import traceback
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500


def get_patient_data(patient_id):
    demographics_query = """
    SELECT 
        e.patient_id,
        COALESCE(MAX(e.age_in_years), 0) as age,
        COALESCE(MAX(e.gender), 'Unknown') as gender,
        COALESCE(MAX(e.race), 'Unknown') as race,
        COALESCE(MAX(e.marital_status), 'Unknown') as marital_status,
        COUNT(DISTINCT e.encounter_id) as total_encounters
    FROM hf_encounter e
    WHERE e.patient_id = %s
    GROUP BY e.patient_id
    """
    
    opioid_summary_query = """
    SELECT 
        COALESCE(COUNT(DISTINCT m.medication_row_id), 0) as total_prescriptions,
        COALESCE(COUNT(DISTINCT m.generic_name), 0) as unique_opioid_types,
        COALESCE(SUM(CASE WHEN m.med_started_dt_tm >= DATE_SUB(NOW(), INTERVAL 30 DAY) THEN 1 ELSE 0 END), 0) as rx_last_30_days,
        COALESCE(SUM(CASE WHEN m.med_started_dt_tm >= DATE_SUB(NOW(), INTERVAL 90 DAY) THEN 1 ELSE 0 END), 0) as rx_last_90_days
    FROM hf_medication m
    JOIN hf_encounter e ON m.encounter_id = e.encounter_id
    WHERE e.patient_id = %s
        AND (LOWER(COALESCE(m.generic_name, '')) LIKE '%oxycodone%' 
             OR LOWER(COALESCE(m.generic_name, '')) LIKE '%hydrocodone%'
             OR LOWER(COALESCE(m.generic_name, '')) LIKE '%morphine%' 
             OR LOWER(COALESCE(m.generic_name, '')) LIKE '%fentanyl%'
             OR LOWER(COALESCE(m.generic_name, '')) LIKE '%codeine%' 
             OR LOWER(COALESCE(m.generic_name, '')) LIKE '%tramadol%')
    """
    
    opioid_details_query = """
    SELECT 
        m.medication_row_id,
        m.encounter_id,
        COALESCE(m.generic_name, 'Unknown') as medication_name,
        COALESCE(m.order_strength, 'N/A') as strength,
        m.med_started_dt_tm as start_date,
        m.med_stopped_dt_tm as stop_date,
        COALESCE(m.duration_minutes, 0) as duration_minutes,
        COALESCE(m.frequency_desc, 'N/A') as frequency,
        COALESCE(DATEDIFF(NOW(), m.med_started_dt_tm), 0) as days_since_prescribed,
        CASE 
            WHEN COALESCE(m.duration_minutes, 0) < 1440 THEN 'Short (<1 day)'
            WHEN COALESCE(m.duration_minutes, 0) < 10080 THEN 'Medium (1-7 days)'
            ELSE 'Long (>7 days)'
        END as duration_category,
        CASE
            WHEN LOWER(COALESCE(m.generic_name, '')) LIKE '%fentanyl%' OR LOWER(COALESCE(m.generic_name, '')) LIKE '%morphine%' THEN 'High Potency'
            WHEN LOWER(COALESCE(m.generic_name, '')) LIKE '%oxycodone%' OR LOWER(COALESCE(m.generic_name, '')) LIKE '%hydrocodone%' THEN 'Medium Potency'
            ELSE 'Low Potency'
        END as potency_level
    FROM hf_medication m
    JOIN hf_encounter e ON m.encounter_id = e.encounter_id
    WHERE e.patient_id = %s
        AND (LOWER(COALESCE(m.generic_name, '')) LIKE '%oxycodone%' 
             OR LOWER(COALESCE(m.generic_name, '')) LIKE '%hydrocodone%'
             OR LOWER(COALESCE(m.generic_name, '')) LIKE '%morphine%' 
             OR LOWER(COALESCE(m.generic_name, '')) LIKE '%fentanyl%'
             OR LOWER(COALESCE(m.generic_name, '')) LIKE '%codeine%' 
             OR LOWER(COALESCE(m.generic_name, '')) LIKE '%tramadol%')
    ORDER BY m.med_started_dt_tm DESC
    """
    
    diagnosis_summary_query = """
    SELECT 
        COALESCE(COUNT(DISTINCT d.diagnosis_row_id), 0) as total_diagnoses,
        COALESCE(SUM(CASE WHEN d.diagnosis_icd LIKE 'F11%' OR d.diagnosis_icd LIKE 'T40%' THEN 1 ELSE 0 END), 0) as opioid_dx,
        COALESCE(SUM(CASE WHEN d.diagnosis_icd LIKE 'F1%' THEN 1 ELSE 0 END), 0) as substance_dx,
        COALESCE(SUM(CASE WHEN d.diagnosis_icd LIKE 'M%' OR d.diagnosis_icd LIKE 'G89%' THEN 1 ELSE 0 END), 0) as pain_dx
    FROM hf_diagnosis d
    JOIN hf_encounter e ON d.encounter_id = e.encounter_id
    WHERE e.patient_id = %s
    """
    
    diagnosis_details_query = """
    SELECT 
        d.diagnosis_row_id,
        d.encounter_id,
        COALESCE(d.diagnosis_icd, 'Unknown') as diagnosis_code,
        COALESCE(d.diagnosis_description, 'Unknown') as diagnosis_description,
        COALESCE(d.diagnosis_priority, 0) as diagnosis_priority,
        COALESCE(d.diagnosis_type, 'Unknown') as diagnosis_type,
        e.admitted_dt_tm as diagnosis_date,
        CASE 
            WHEN d.diagnosis_icd LIKE 'F11%' THEN 'Opioid Use'
            WHEN d.diagnosis_icd LIKE 'T40%' THEN 'Opioid Poisoning'
            WHEN d.diagnosis_icd LIKE 'F1%' THEN 'Substance Use'
            WHEN d.diagnosis_icd LIKE 'M%' THEN 'Pain'
            ELSE 'Other'
        END as diagnosis_category
    FROM hf_diagnosis d
    JOIN hf_encounter e ON d.encounter_id = e.encounter_id
    WHERE e.patient_id = %s
    ORDER BY e.admitted_dt_tm DESC
    """
    
    
    encounter_summary_query = """
    SELECT 
        COALESCE(COUNT(DISTINCT e.encounter_id), 0) as total_encounters,
        COALESCE(SUM(CASE WHEN LOWER(COALESCE(e.patient_type_desc, '')) LIKE '%emergency%' THEN 1 ELSE 0 END), 0) as ed_visits,
        COALESCE(SUM(CASE WHEN LOWER(COALESCE(e.patient_type_desc, '')) LIKE '%inpatient%' THEN 1 ELSE 0 END), 0) as inpatient_stays,
        COALESCE(AVG(DATEDIFF(e.discharged_dt_tm, e.admitted_dt_tm)), 0) as avg_los
    FROM hf_encounter e
    WHERE e.patient_id = %s
    """
    
   
    encounter_details_query = """
    SELECT 
        e.encounter_id,
        e.admitted_dt_tm as admission_date,
        e.discharged_dt_tm as discharge_date,
        COALESCE(DATEDIFF(e.discharged_dt_tm, e.admitted_dt_tm), 0) as length_of_stay_days,
        COALESCE(e.patient_type_desc, 'Unknown') as encounter_type,
        COALESCE(e.dischg_disp_code_desc, 'Unknown') as discharge_disposition,
        COALESCE(e.caresetting_desc, 'Unknown') as care_setting,
        COALESCE(e.payer_code_desc, 'Unknown') as payer
    FROM hf_encounter e
    WHERE e.patient_id = %s
    ORDER BY e.admitted_dt_tm DESC
    """
    
    
    demographics = query_database(demographics_query, (patient_id,))
    opioid_summary = query_database(opioid_summary_query, (patient_id,))
    opioid_details = query_database(opioid_details_query, (patient_id,))
    diagnosis_summary = query_database(diagnosis_summary_query, (patient_id,))
    diagnosis_details = query_database(diagnosis_details_query, (patient_id,))
    encounter_summary = query_database(encounter_summary_query, (patient_id,))
    encounter_details = query_database(encounter_details_query, (patient_id,))
    
   
    risk = calculate_risk(opioid_summary[0] if opioid_summary else {},
                         diagnosis_summary[0] if diagnosis_summary else {},
                         encounter_summary[0] if encounter_summary else {})
    
    return {
        'patient_id': patient_id,
        'demographics': demographics[0] if demographics else {},
        'opioid_summary': opioid_summary[0] if opioid_summary else {},
        'opioid_details': opioid_details,
        'diagnosis_summary': diagnosis_summary[0] if diagnosis_summary else {},
        'diagnosis_details': diagnosis_details,
        'encounter_summary': encounter_summary[0] if encounter_summary else {},
        'encounter_details': encounter_details,
        'risk_score': risk
    }


def calculate_risk(opioid, diagnosis, encounter):
    """Calculate risk score 0 to 100"""
    score = 0
    factors = []
    
    total_rx = opioid.get('total_prescriptions', 0) or 0
    if total_rx >= 10:
        score += 20
        factors.append('High Prescription Count')
    elif total_rx >= 5:
        score += 10
    
    recent_rx = opioid.get('rx_last_30_days', 0) or 0
    if recent_rx >= 2:
        score += 15
        factors.append('Recent Prescriptions')
    
    if (diagnosis.get('opioid_dx', 0) or 0) > 0:
        score += 30
        factors.append('Opioid Use Disorder')
    
    if (diagnosis.get('substance_dx', 0) or 0) > 0:
        score += 15
        factors.append('Substance Use History')
    
    ed_visits = encounter.get('ed_visits', 0) or 0
    if ed_visits >= 3:
        score += 10
        factors.append('Frequent ED Visits')
    
    if score >= 60:
        level = 'CRITICAL'
    elif score >= 40:
        level = 'HIGH'
    elif score >= 20:
        level = 'MODERATE'
    else:
        level = 'LOW'
    
    return {'score': min(score, 100), 'level': level, 'factors': factors}


def flatten_for_tableau(patient_data):
    rows = []
    patient_id = patient_data['patient_id']
    
    def safe(data, key, default=None):
        value = data.get(key, default)
        return value if value is not None else default
    
    demo = patient_data.get('demographics', {})
    risk = patient_data.get('risk_score', {})
    
    base = {
        'patient_id': patient_id,
        'age': safe(demo, 'age', 0),
        'gender': safe(demo, 'gender', 'Unknown'),
        'race': safe(demo, 'race', 'Unknown'),
        'marital_status': safe(demo, 'marital_status', 'Unknown'),
        'total_encounters': safe(demo, 'total_encounters', 0),
        'risk_score': safe(risk, 'score', 0),
        'risk_level': safe(risk, 'level', 'LOW'),
        'risk_factors': ', '.join(risk.get('factors', []))
    }
    
   
    for med in patient_data.get('opioid_details', []):
        row = base.copy()
        row.update({
            'data_type': 'Medication',
            'medication_name': safe(med, 'medication_name', 'Unknown'),
            'strength': safe(med, 'strength', 'N/A'),
            'start_date': safe(med, 'start_date'),
            'stop_date': safe(med, 'stop_date'),
            'duration_minutes': safe(med, 'duration_minutes', 0),
            'frequency': safe(med, 'frequency', 'N/A'),
            'days_since_prescribed': safe(med, 'days_since_prescribed', 0),
            'duration_category': safe(med, 'duration_category', 'Unknown'),
            'potency_level': safe(med, 'potency_level', 'Unknown'),
            'encounter_id': safe(med, 'encounter_id')
        })
        rows.append(row)
    
    for diag in patient_data.get('diagnosis_details', []):
        row = base.copy()
        row.update({
            'data_type': 'Diagnosis',
            'diagnosis_code': safe(diag, 'diagnosis_code', 'Unknown'),
            'diagnosis_description': safe(diag, 'diagnosis_description', 'Unknown'),
            'diagnosis_priority': safe(diag, 'diagnosis_priority', 0),
            'diagnosis_type': safe(diag, 'diagnosis_type', 'Unknown'),
            'diagnosis_category': safe(diag, 'diagnosis_category', 'Other'),
            'diagnosis_date': safe(diag, 'diagnosis_date'),
            'encounter_id': safe(diag, 'encounter_id')
        })
        rows.append(row)
    
    for enc in patient_data.get('encounter_details', []):
        row = base.copy()
        row.update({
            'data_type': 'Encounter',
            'encounter_id': safe(enc, 'encounter_id'),
            'admission_date': safe(enc, 'admission_date'),
            'discharge_date': safe(enc, 'discharge_date'),
            'length_of_stay_days': safe(enc, 'length_of_stay_days', 0),
            'encounter_type': safe(enc, 'encounter_type', 'Unknown'),
            'patient_type': safe(enc, 'encounter_type', 'Unknown'),
            'discharge_disposition': safe(enc, 'discharge_disposition', 'Unknown'),
            'care_setting': safe(enc, 'care_setting', 'Unknown'),
            'payer': safe(enc, 'payer', 'Unknown')
        })
        rows.append(row)
    
    opioid_sum = patient_data.get('opioid_summary', {})
    if opioid_sum and opioid_sum.get('total_prescriptions'):
        row = base.copy()
        row.update({
            'data_type': 'Summary',
            'total_prescriptions': safe(opioid_sum, 'total_prescriptions', 0),
            'unique_opioid_types': safe(opioid_sum, 'unique_opioid_types', 0),
            'rx_last_30_days': safe(opioid_sum, 'rx_last_30_days', 0),
            'rx_last_90_days': safe(opioid_sum, 'rx_last_90_days', 0)
        })
        rows.append(row)
    
    if not rows:
        row = base.copy()
        row['data_type'] = 'Demographics'
        rows.append(row)
    
    return rows


@app.route('/')
def index():
    return render_template('dashboard1.html')



@app.route('/api/tableau/patient/<int:patient_id>')
def get_tableau_data(patient_id):
    try:
        data = get_patient_data(patient_id)
        tableau_data = flatten_for_tableau(data)
        return jsonify(tableau_data)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/test/connection')
def test_connection():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT COUNT(*) as count FROM hf_encounter")
        encounter_count = cursor.fetchone()
        
        cursor.execute("SELECT DISTINCT patient_id FROM hf_encounter LIMIT 10")
        patients = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'status': 'SUCCESS',
            'encounter_count': encounter_count['count'],
            'sample_patients': [p['patient_id'] for p in patients]
        })
    except Exception as e:
        return jsonify({'status': 'FAILED', 'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

