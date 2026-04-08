import streamlit as st
import pandas as pd
import pdfplumber
import mysql.connector
import io
import re

# 1. CONEXÃO
def get_connection():
    return mysql.connector.connect(
        host=st.secrets["mysql"]["host"],
        user=st.secrets["mysql"]["user"],
        password=st.secrets["mysql"]["password"],
        database=st.secrets["mysql"]["database"]
    )

# 2. LEITOR COM SUPORTE A HORAS (SEPARADOR DE TRANSAÇÕES)
@st.cache_data(show_spinner=False)
def extrair_texto_pdf(file_bytes):
    texto_completo = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t: texto_completo += t + "\n"
            
    linhas_brutas = texto_completo.split('\n')
    dados_extraidos = []
    transacao_atual = None
    
    for linha in linhas_brutas:
        linha = linha.strip()
        if not linha: continue
        
        # GATILHO: Nova transação começa com DATA (00/00) ou HORA (00:00)
        tem_data = re.search(r'(\d{2}/\d{2}/\d{2,4})', linha)
        tem_hora = re.search(r'(\d{2}:\d{2})', linha)
        
        if tem_data or tem_hora:
            if transacao_atual:
                dados_extraidos.append(transacao_atual)
            
            data = tem_data.group(1) if tem_data else (transacao_atual['Data'] if transacao_atual else "")
            sinal = '+' if 'Entrada' in linha else '-'
            valores = re.findall(r'\d{1,3}(?:\.\d{3})*,\d{2}', linha)
            valor_num = float(valores[0].replace('.', '').replace(',', '.')) if valores else 0.0
            
            # Limpeza da descrição (remove data, hora e valores)
            desc = linha
            if tem_data: desc = desc.replace(tem_data.group(0), '')
            if tem_hora: desc = desc.replace(tem_hora.group(0), '')
            for v in valores: desc = desc.replace(v, '')
            desc = desc.replace('Entrada', '').replace('Saída', '').replace('R$', '').replace('-', '').strip()
            
            transacao_atual = {'Data': data, 'Descricao': desc, 'Valor': abs(valor_num), 'Sinal': sinal}
        else:
            if transacao_atual and "Saldo" not in linha and "Página" not in linha:
                transacao_atual['Descricao'] += f" {linha.replace('R$', '').strip()}"
                
    if transacao_atual: dados_extraidos.append(transacao_atual)
    return pd.DataFrame(dados_extraidos)

# 3. INTERFACE
st.set_page_config(page_title="Conciliador", page_icon="🎯", layout="wide")
st.title("🎯 Conciliador Pro - Stone")

# Setup Empresa e Banco
col_cfg1, col_cfg2 = st.columns([2, 1])
conn = get_connection()
empresas = pd.read_sql("SELECT id, nome FROM empresas", conn)
empresa_sel = col_cfg1.selectbox("Empresa / Filial", empresas['nome'])
id_empresa = int(empresas[empresas['nome'] == empresa_sel]['id'].values[0])
conta_banco_fixa = col_cfg2.text_input("Conta do Banco (Âncora)", value="196")

uploaded_file = st.file_uploader("Suba o PDF da Stone", type="pdf")

if uploaded_file and conta_banco_fixa:
    df_bruto = extrair_texto_pdf(uploaded_file.getvalue())
    regras = pd.read_sql(f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa}", conn)

    prontos, pendentes = [], []
    for _, row in df_bruto.iterrows():
        match = False
        for _, r in regras.iterrows():
            if r['termo_chave'].upper() in row['Descricao'].upper() and r['sinal_esperado'] == row['Sinal']:
                d = conta_banco_fixa if row['Sinal'] == '+' else r['conta_contabil']
                c = r['conta_contabil'] if row['Sinal'] == '+' else conta_banco_fixa
                prontos.append({
                    'Debito': d, 'Credito': c, 'Data': row['Data'], 
                    'Valor': f"{row['Valor']:.2f}".replace('.', ','),
                    'Historico': r['historico_padrao'] if r['historico_padrao'] else row['Descricao'],
                    'Cod. Hist': str(int(r['cod_historico_erp'])) if pd.notna(r['cod_historico_erp']) else ""
                })
                match = True; break
        if not match: pendentes.append(row)

    # Painel de Status
    c1, c2 = st.columns(2)
    c1.metric("Prontos para ERP", f"{len(prontos)} linhas")
    c2.metric("Pendentes", f"{len(pendentes)} linhas", delta_color="inverse")

    if not pendentes and prontos:
        st.success("✅ Tudo mapeado!")
        st.download_button("📥 BAIXAR CSV ALTERDATA", pd.DataFrame(prontos).to_csv(index=False, sep=';', encoding='latin1'), "importar.csv", "text/csv", type="primary")
    
    # Mesa de Treinamento
    if pendentes:
        st.warning("🔒 Exportação bloqueada até resolver as pendências abaixo.")
        df_p = pd.DataFrame(pendentes)
        top = df_p.groupby(['Descricao', 'Sinal']).size().reset_index(name='Qtd').sort_values('Qtd', ascending=False).iloc[0]
        
        with st.form("treinar", clear_on_submit=True):
            st.write(f"**Resolvendo {top['Qtd']} lançamentos de:** `{top['Descricao']}`")
            t_chave = st.text_input("Termo Chave (Corte o que for variável)", value=top['Descricao'])
            col1, col2, col3 = st.columns(3)
            c_contra = col1.text_input("Conta Contrapartida")
            c_hist = col2.text_input("Cód. Hist.")
            t_hist = col3.text_input("Histórico Padrão")
            if st.form_submit_button("Salvar Inteligência"):
                cursor = conn.cursor()
                cursor.execute("INSERT INTO tb_extratos_regras (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil, cod_historico_erp, historico_padrao) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                             (id_empresa, 'STONE', t_chave.upper(), top['Sinal'], c_contra, c_hist, t_hist))
                conn.commit(); st.rerun()

# GERENCIADOR DE REGRAS (O SEU CADASTRO)
st.divider()
with st.expander("📖 Ver Minha Inteligência (Regras Salvas no Banco UOL)"):
    minhas_regras = pd.read_sql(f"SELECT id, termo_chave, sinal_esperado, conta_contabil as contrapartida, historico_padrao FROM tb_extratos_regras WHERE id_empresa = {id_empresa}", conn)
    st.dataframe(minhas_regras, use_container_width=True)
    
    id_del = st.number_input("ID da regra para excluir", step=1, value=0)
    if st.button("Excluir Regra"):
        cursor = conn.cursor()
        cursor.execute(f"DELETE FROM tb_extratos_regras WHERE id = {id_del}")
        conn.commit(); st.rerun()

conn.close()
