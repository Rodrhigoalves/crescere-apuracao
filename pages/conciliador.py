import streamlit as st
import pandas as pd
import pdfplumber
import mysql.connector
import io
import re
import unicodedata
from thefuzz import fuzz

# ---------------------------------------------------------
# 1. UTILITÁRIOS E CONEXÃO
# ---------------------------------------------------------
def get_connection():
    return mysql.connector.connect(
        host=st.secrets["mysql"]["host"],
        user=st.secrets["mysql"]["user"],
        password=st.secrets["mysql"]["password"],
        database=st.secrets["mysql"]["database"]
    )

def padronizar_texto(texto):
    if not texto: return ""
    texto_sem_acento = unicodedata.normalize('NFKD', str(texto)).encode('ASCII', 'ignore').decode('utf-8')
    texto_limpo = re.sub(r'\s+', ' ', texto_sem_acento.upper().strip())
    return texto_limpo

# ---------------------------------------------------------
# 2. MOTOR DE RECINTOS (Extração por Blocos)
# ---------------------------------------------------------
@st.cache_data(show_spinner=False)
def extrair_por_recintos(file_bytes):
    texto_completo = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t: texto_completo += t + "\n"
            
    linhas = texto_completo.split('\n')
    blocos = []
    bloco_atual = None
    
    for linha in linhas:
        linha = linha.strip()
        if not linha or any(x in linha.lower() for x in ["período:", "página", "saldo", "stone institui", "data tipo descri"]):
            continue
            
        tem_data = re.search(r'(\d{2}/\d{2}/\d{2,4})', linha)
        valores = re.findall(r'\b\d{1,3}(?:\.\d{3})*,\d{2}\b', linha)
        
        # Início de um Novo Recinto (Sempre que houver um Valor)
        if valores:
            if bloco_atual:
                blocos.append(bloco_atual)
            
            valor_num = float(valores[0].replace('.', '').replace(',', '.'))
            sinal = '+' if 'Entrada' in linha or valor_num > 0 else '-'
            data = tem_data.group(1) if tem_data else (blocos[-1]['Data'] if blocos else "")
            
            # Limpa a linha do valor para pegar o texto que está nela
            desc_lin = linha
            if tem_data: desc_lin = desc_lin.replace(tem_data.group(0), '')
            for v in valores: desc_lin = desc_lin.replace(v, '')
            desc_lin = re.sub(r'\b(Entrada|Saída|R\$|-)\b', '', desc_lin, flags=re.IGNORECASE).strip()
            
            bloco_atual = {'Data': data, 'Descricao': desc_lin, 'Valor': abs(valor_num), 'Sinal': sinal}
        else:
            # Texto flutuante: Adiciona à descrição do bloco atual
            if bloco_atual:
                bloco_atual['Descricao'] += " " + linha.replace('R$', '').strip()
                
    if bloco_atual: blocos.append(bloco_atual)
    df = pd.DataFrame(blocos)
    df['Descricao'] = df['Descricao'].apply(padronizar_texto)
    return df

# ---------------------------------------------------------
# 3. INTERFACE E LÓGICA DE ESTADO
# ---------------------------------------------------------
st.set_page_config(page_title="Conciliador Pro", page_icon="🎯", layout="wide")

if 'skipped_indices' not in st.session_state:
    st.session_state.skipped_indices = []

st.title("🎯 Conciliador Pro - Mesa por Cliques")

# Setup Empresa
conn = get_connection()
empresas = pd.read_sql("SELECT id, nome FROM empresas", conn)
col_cfg1, col_cfg2 = st.columns([2, 1])
empresa_sel = col_cfg1.selectbox("Empresa / Filial", empresas['nome'])
id_empresa = int(empresas[empresas['nome'] == empresa_sel]['id'].values[0])
conta_banco_fixa = col_cfg2.text_input("Conta Banco (196)", value="196")

uploaded_files = st.file_uploader("Arraste seus PDFs aqui", type="pdf", accept_multiple_files=True)

if uploaded_files and conta_banco_fixa:
    with st.spinner("Escaneando recintos e mapeando dados..."):
        lista_dfs = [extrair_por_recintos(f.getvalue()) for f in uploaded_files]
        df_bruto = pd.concat(lista_dfs, ignore_index=True)
        regras = pd.read_sql(f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa}", conn)

    prontos, pendentes = [], []
    
    # Processamento de Regras (Fuzzy 85%)
    for idx, row in df_bruto.iterrows():
        match = False
        for _, r in regras.iterrows():
            if fuzz.partial_ratio(padronizar_texto(r['termo_chave']), row['Descricao']) >= 85 and r['sinal_esperado'] == row['Sinal']:
                if r['conta_contabil'] != 'IGNORAR':
                    d = conta_banco_fixa if row['Sinal'] == '+' else r['conta_contabil']
                    c = r['conta_contabil'] if row['Sinal'] == '+' else conta_banco_fixa
                    prontos.append({
                        'Debito': d, 'Credito': c, 'Data': row['Data'], 
                        'Valor': f"{row['Valor']:.2f}".replace('.', ','),
                        'Historico': r['historico_padrao'] if r['historico_padrao'] else row['Descricao']
                    })
                match = True; break
        if not match:
            pendentes.append({'idx_original': idx, **row})

    # Gerenciar Fila de Pendentes (Respeitando os Pulados)
    df_p = pd.DataFrame(pendentes)
    if not df_p.empty:
        # Filtra os que não foram pulados nesta rodada
        fila_atual = df_p[~df_p['idx_original'].isin(st.session_state.skipped_indices)]
        
        # Se a fila atual acabar mas houver pulados, reinicia os pulados no final
        if fila_atual.empty and st.session_state.skipped_indices:
            st.session_state.skipped_indices = []
            st.rerun()
            
        st.metric("Lançamentos Pendentes", len(df_p))
        
        if not fila_atual.empty:
            item = fila_atual.iloc[0]
            st.divider()
            st.subheader("🎓 Mesa de Treinamento por Cliques")
            
            # --- INTERFACE DE CLIQUES ---
            palavras = item['Descricao'].split()
            st.write("**Selecione as palavras para criar a regra:**")
            selecionadas = st.pills("Palavras da Descrição", palavras, selection_mode="multi", label_visibility="collapsed")
            
            termo_final = " ".join(selecionadas) if selecionadas else ""
            st.text_input("Termo Chave Gerado", value=termo_final, disabled=True)
            
            # Contador de Impacto
            if termo_final:
                impacto = df_p[df_p['Descricao'].str.contains(termo_final)]['idx_original'].count()
                st.info(f"💡 Esta regra vai resolver **{impacto}** lançamentos de uma vez.")

            with st.form("form_treino"):
                c1, c2, c3 = st.columns(3)
                contra = c1.text_input("Contrapartida")
                cod_h = c2.text_input("Cód. Hist.")
                txt_h = c3.text_input("Histórico Padrão")
                
                b1, b2, b3, b4 = st.columns(4)
                if b1.form_submit_button("✅ Salvar Regra", use_container_width=True):
                    if termo_final and contra:
                        cursor = conn.cursor()
                        cursor.execute("INSERT INTO tb_extratos_regras (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil, cod_historico_erp, historico_padrao) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                                     (id_empresa, 'PADRAO', termo_final, item['Sinal'], contra, cod_h, txt_h))
                        conn.commit(); st.rerun()
                
                if b2.form_submit_button("🗑️ Ignorar Lixo", use_container_width=True):
                    if termo_final:
                        cursor = conn.cursor()
                        cursor.execute("INSERT INTO tb_extratos_regras (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil) VALUES (%s, %s, %s, %s, %s)",
                                     (id_empresa, 'PADRAO', termo_final, item['Sinal'], 'IGNORAR'))
                        conn.commit(); st.rerun()

                if b3.form_submit_button("⏭️ Pular", use_container_width=True):
                    st.session_state.skipped_indices.append(item['idx_original'])
                    st.rerun()
                
                if b4.form_submit_button("🔄 Resetar Fila", use_container_width=True):
                    st.session_state.skipped_indices = []
                    st.rerun()

    # 4. DOWNLOAD (TRAVADO)
    if not prontos:
        st.info("Aguardando processamento...")
    elif not pendentes:
        st.success("🎉 Tudo mapeado! Exportação liberada.")
        st.download_button("📥 BAIXAR CSV ALTERDATA", pd.DataFrame(prontos).to_csv(index=False, sep=';', encoding='latin1'), "importar.csv", "text/csv", type="primary")
    else:
        st.warning(f"🔒 Bloqueado: Ainda restam {len(pendentes)} itens para conciliar.")

conn.close()
