import streamlit as st
import pandas as pd
import pdfplumber
import mysql.connector

# Conexão usando os Secrets que você já configurou
def get_connection():
    return mysql.connector.connect(
        host=st.secrets["mysql"]["host"],
        user=st.secrets["mysql"]["user"],
        password=st.secrets["mysql"]["password"],
        database=st.secrets["mysql"]["database"]
    )

def processar_stone(file, id_empresa):
    regras = pd.read_sql(f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa}", get_connection())
    
    dados_extrato = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if table:
                for row in table[1:]:
                    # Ajuste os índices [0, 2, 3] conforme o layout real do PDF da Stone
                    data, desc, valor_str = row[0], row[2], row[3]
                    valor_num = float(valor_str.replace('R$', '').replace('.', '').replace(',', '.').strip())
                    sinal = '-' if valor_num < 0 else '+'
                    
                    # Busca De-Para no Banco
                    match = "NAO_MAPEADO"
                    conta_dc = ""
                    cod_hist = ""
                    hist_final = desc.replace('\n', ' ')
                    
                    for _, r in regras.iterrows():
                        if r['termo_chave'].upper() in hist_final.upper() and r['sinal_esperado'] == sinal:
                            match = r['conta_contabil']
                            conta_dc = r['conta_contrapartida_padrao']
                            cod_hist = r['cod_historico_erp']
                            hist_final = r['historico_padrao']
                            break
                    
                    dados_extrato.append({
                        'Data': data, 'Desc': hist_final, 'Valor': abs(valor_num), 
                        'Sinal': sinal, 'Conta': match, 'Contra': conta_dc, 'CodHist': cod_hist
                    })

    # Montagem das 11 colunas do seu modelo Alterdata
    rows_alterdata = []
    for d in dados_extrato:
        rows_alterdata.append({
            'Debito': d['Conta'] if d['Sinal'] == '-' else d['Contra'],
            'Credito': d['Contra'] if d['Sinal'] == '-' else d['Conta'],
            'Data': d['Data'],
            'Valor': f"{d['Valor']:.2f}".replace('.', ','),
            'Cod. Historico': d['CodHist'] if d['CodHist'] else '',
            'Historico': d['Desc'] if d['Desc'] else 'Lancamento Stone',
            'Nr. Documento': '', 'Cod. Centro Custo': '', 'Livro': '', 'Filial': '', 'Tipo': ''
        })
    
    return pd.DataFrame(rows_alterdata)

# Interface Streamlit
st.title("📂 Conciliador de Extratos Stone")

# Seleção da Empresa (Busca da sua tabela 'empresas' já existente)
conn = get_connection()
empresas = pd.read_sql("SELECT id, nome FROM empresas", conn)
empresa_sel = st.selectbox("Selecione a Empresa/Filial", empresas['nome'])
id_empresa = empresas[empresas['nome'] == empresa_sel]['id'].values[0]

uploaded_file = st.file_uploader("Suba o PDF da Stone", type="pdf")

if uploaded_file:
    df_final = processar_stone(uploaded_file, id_empresa)
    st.write("### Prévia para o Alterdata")
    st.dataframe(df_final)
    
    csv = df_final.to_csv(index=False, sep=';', encoding='latin1').encode('latin1')
    st.download_button("📥 Baixar CSV para Alterdata", csv, "importar_alterdata.csv", "text/csv")
