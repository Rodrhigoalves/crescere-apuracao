import mysql.connector
import streamlit as st

def query_banco(sql):
    # Use os dados de conexão que você já tem nos outros scripts
    conn = mysql.connector.connect(
        host=st.secrets["mysql"]["host"],
        user=st.secrets["mysql"]["user"],
        password=st.secrets["mysql"]["password"],
        database=st.secrets["mysql"]["database"]
    )
    cursor = conn.cursor(dictionary=True)
    cursor.execute(sql)
    
    if sql.strip().upper().startswith("SELECT"):
        result = cursor.fetchall()
    else:
        conn.commit()
        result = None
        
    cursor.close()
    conn.close()
    return result
