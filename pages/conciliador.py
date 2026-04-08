import streamlit as st
import pandas as pd
import pdfplumber
import mysql.connector

# 1. Ligação ao Banco de Dados UOL (usando os Secrets)
def get_connection():
    return mysql.connector.connect(
        host=st.secrets["mysql"]["host"],
        user=st.secrets["mysql"]["user"],
        password=st.secrets["mysql"]["password"],
        database=st.secrets["mysql"]["database"]
    )

# 2. Motor de Processamento do Extrato Stone
def processar_stone(file, id_empresa):
    # Vai buscar as regras específicas desta empresa ao UOL
    conn = get_connection()
    query = f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa} AND banco_nome = 'STONE'"
    regras = pd.read_sql(query, conn)
    conn.close()
    
    dados_extrato = []
    
    # Extração de dados usando leitura de texto (mais robusto que tabelas)
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            texto = page.extract_text()
            if not texto:
                continue
                
            linhas = texto.split('\n')
            for linha in linhas:
                # Foca apenas nas linhas que começam com uma data (ex: 01/01/2026)
                if len(linha) > 10 and linha[2] == '/' and linha[5] == '/':
                    partes = linha.split()
                    
                    try:
                        data = partes[0]
                        valor_str = partes[-1] # O valor é sempre a última informação da linha
                        desc_original = " ".join(partes[1:-1]) # A descrição é o que está no meio
                        
                        # Limpeza e conversão do Valor
                        valor_limpo = valor_str.replace('R$', '').replace('.', '').replace(',', '.').strip()
                        valor_num = float(valor_limpo)
                        
                        # Identificação da Natureza (Entrada ou Saída)
                        sinal = '-' if '-' in valor_str or valor_num < 0 else '+'
                        valor_absoluto = abs(valor_num) # O ERP só aceita números positivos
                        
                        # Valores padrão caso não encontre regra
                        match_conta = ""
                        match_contra = ""
                        match_cod_hist = ""
                        match_hist = desc_original
                        
                        # Cruzamento com o Dicionário De-Para
                        for _, r in regras.iterrows():
                            # Procura o termo dentro da descrição e verifica se o sinal bate
                            if r['termo_chave'].upper() in desc_original.upper() and r['sinal_esperado'] == sinal:
                                match_conta = r['conta_contabil']
                                match_contra = r['conta_contrapartida_padrao']
                                match_cod_hist = r['cod_historico_erp'] if pd.notna(r['cod_historico_erp']) else ""
                                match_hist = r['historico_padrao'] if pd.notna(r['historico_padrao']) else desc_original
                                break
                        
                        dados_extrato.append({
                            'Data': data, 
                            'Desc': match_hist, 
                            'Valor': valor_absoluto, 
                            'Sinal': sinal, 
                            'Conta': match_conta, 
                            'Contra': match_contra, 
                            'CodHist': match_cod_hist
                        })
                    except Exception as e:
                        # Ignora linhas que não consiga converter de forma segura
                        continue 

    # 3. Montagem do Ficheiro Final (11 Colunas para o Alterdata)
    linhas_alterdata = []
    for d in dados_extrato:
        # Lógica de Partidas Dobradas
        conta_debito = d['Conta'] if d['Sinal'] == '-' else d['Contra']
        conta_credito = d['Contra'] if d['Sinal'] == '-' else d['Conta']
        
        # Garantia de Histórico preenchido
        texto_hist = d['Desc'] if d['Desc'] else 'Lancamento Stone'
        codigo_hist = str(int(d['CodHist'])) if d['CodHist'] else ''

        linhas_alterdata.append({
            'Debito': conta_debito,
            'Credito': conta_credito,
            'Data': d['Data'],
            'Valor': f"{d['Valor']:.2f}".replace('.', ','), # Formato contábil PT/BR
            'Cod. Historico': codigo_hist,
            'Historico': texto_hist,
            'Nr. Documento': '', 
            'Cod. Centro Custo': '', 
            'Livro': '', 
            'Filial': '', 
            'Tipo': ''
        })
    
    return pd.DataFrame(linhas_alterdata)

# --- INTERFACE STREAMLIT ---
st.set_page_config(page_title="Conciliador Stone", page_icon="🧾")
st.title("🧾 Conciliador de Extratos - Stone")

# Busca as empresas ao banco de dados para o utilizador escolher
try:
    conn = get_connection()
    empresas = pd.read_sql("SELECT id, nome FROM empresas", conn)
    conn.close()
    
    empresa_sel = st.selectbox("Selecione a Empresa/Filial", empresas['nome'])
    id_empresa = empresas[empresas['nome'] == empresa_sel]['id'].values[0]
except Exception as e:
    st.error("Erro ao carregar empresas do banco de dados. Verifique a ligação.")
    st.stop()

uploaded_file = st.file_uploader("Faça upload do PDF da Stone", type="pdf")

if uploaded_file:
    with st.spinner("A processar e a cruzar dados..."):
        df_final = processar_stone(uploaded_file, id_empresa)
        
        if df_final.empty:
            st.warning("Não foi possível extrair lançamentos válidos deste PDF.")
        else:
            st.success("Extrato processado com sucesso!")
            st.write("### Pré-visualização para o Alterdata")
            st.dataframe(df_final)
            
            # Botão de Download do CSV no formato correto
            csv = df_final.to_csv(index=False, sep=';', encoding='latin1')
            st.download_button(
                label="📥 Baixar Ficheiro para o Alterdata",
                data=csv,
                file_name=f"importar_stone_{empresa_sel.replace(' ', '_')}.csv",
                mime="text/csv"
            )
