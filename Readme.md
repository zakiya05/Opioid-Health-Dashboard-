# PCORI Dashboard Backend

This project is a **Flask-based application** that establishes a secure connection to a MySQL database via an **SSH tunnel**. It serves as the data engine for the PCORI dashboard visualizations.

## Step 1: Prerequisites

Ensure you have Python installed, then install the required dependencies using the command below:

```bash
pip install flask flask-cors mysql-connector-python sshtunnel

```

The application leverages the following key libraries:

* `flask`, `jsonify`, `render_template`
* `mysql.connector`
* `sshtunnel`
* `flask_cors`
* `decimal`, `datetime`

---

##  Step 2: Configuration

Before starting the application, you must update your credentials. Open the **`config/const.py`** file and enter your specific details:

```python
# SSH Connection Constants
SSH_USER = 'Your User Name'
SSH_PASS = 'Your Password'

# Database Constants
DB_USER = 'DB user'                
DB_PASS = 'DB password'

```

---

## Step 3: Run & View

### 1. Start the Flask Server

Run the following command in your terminal to launch the backend:

```bash
python app.py 

```

### 2. View Analytics

Once the backend is running and the connection is established:

1. Navigate to the **Tableau** folder within this project.
2. **Open each dashboard** file individually to view and interact with the data visualizations.
