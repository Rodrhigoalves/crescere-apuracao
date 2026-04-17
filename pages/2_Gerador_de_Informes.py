import streamlit as st
import os

st.title("🔎 Verificador de Caminhos")

# Mostra onde o script atual está executando
st.write(f"**Caminho deste script:** {os.path.abspath(__file__)}")

# Mostra a pasta raiz do projeto
st.write(f"**Diretório de trabalho (Raiz):** {os.getcwd()}")

# Lista todos os arquivos na raiz para ver se o Word está lá
st.write("**Arquivos na Raiz do Projeto:**")
st.code(os.listdir("."))

# Lista arquivos dentro da pasta pages
if os.path.exists("pages"):
    st.write("**Arquivos dentro da pasta /pages:**")
    st.code(os.listdir("pages"))
