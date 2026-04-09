import streamlit as st
import pandas as pd
import pdfplumber
import mysql.connector
import io
import re
import unicodedata
from thefuzz import fuzz
from ofxparse import OfxParser

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
# 2. MOTOR DE EXTRAÇÃO PDF (SPLIT POR ÂNCORA DATA+TIPO)
# ---------------------------------------------------------
def _extrair_nome_final(chunk: str) -> str:
    """
    Remove valores monetários, palavras-chave e sinais do final do chunk.
    O que sobra é o nome da contraparte da próxima transação.
    """
    texto = re.sub(r'\d{1,3}(?:\.\d{3})*,\d{2}', '', chunk)
    texto = re.sub(r'R\$|\bS\.A\b\.?', '', texto)
    texto = re.sub(r'\b(Parcela|Empréstimo|Transferência|Pix|Maquininha|Débito|Crédito|Tarifa)\b',
                   '', texto, flags=re.IGNORECASE)
    texto = re.sub(r'[|\-]', ' ', texto)
    texto = re.sub(r'\s+', ' ', texto).strip()

    # Pega apenas as últimas palavras que parecem um nome (maiúsculas)
    tokens = texto.strip().split()
    nome_tokens = []
    for tok in reversed(tokens):
        if re.match(r'^[A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇÀ]{2,}$', tok):
            nome_tokens.insert(0, tok)
        else:
            break  # Para ao encontrar token que não é nome em maiúsculo

    return " ".join(nome_tokens)

@st.cache_data(show_spinner=False)
def extrair_por_recintos(file_bytes):
    texto_completo = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t: texto_completo += t + "\n"

    # Junta tudo numa string, removendo cabeçalhos conhecidos
    RUIDO = ["período:", "página", "saldo anterior", "saldo atual", "saldo final",
             "data tipo descri", "cnpj", "emitido em", "extrato de conta",
             "dados da conta", "nome documento", "instituição agência",
             "contraparte stone"]
    linhas = [l.strip() for l in texto_completo.split('\n')
              if l.strip() and not any(x in l.lower() for x in RUIDO)]
    texto = " ".join(linhas)

    # -------------------------------------------------------
    # ESTRATÉGIA: Split por âncora DATA + TIPO
    # O texto fica: [CONTRAPARTE_A] DATA TIPO [valor...] [CONTRAPARTE_B] DATA TIPO ...
    # Cada fatia entre duas âncoras = 1 transação
    # -------------------------------------------------------
    ANCHOR = r'(\d{2}/\d{2}/\d{2,4})\s+(Saída|Entrada|Saque|Depósito)'
    partes = re.split(ANCHOR, texto)
    # partes = [lixo_inicial, data1, tipo1, corpo1, data2, tipo2, corpo2, ...]

    n = (len(partes) - 1) // 3
    dados = []

    for i in range(n):
        data  = partes[i * 3 + 1]
        tipo  = partes[i * 3 + 2]
        corpo = partes[i * 3 + 3].strip()

        # A contraparte desta transação está no FINAL do chunk anterior
        chunk_anterior = partes[(i - 1) * 3 + 3] if i > 0 else partes[0]
        contraparte = _extrair_nome_final(chunk_anterior)

        # Valores: primeiro = valor da tx, segundo = saldo (descartamos saldo)
        valores = re.findall(r'\d{1,3}(?:\.\d{3})*,\d{2}', corpo)
        if not valores:
            continue
        valor_num = float(valores[0].replace('.', '').replace(',', '.'))
        sinal = '+' if tipo.lower() == 'entrada' else '-'

        # Subcategoria: padrão "Palavra | Palavra" ou "Tarifa"
        sub = re.search(r'([A-ZÀ-Úa-zà-ú]+(?:\s+[A-ZÀ-Úa-zà-ú]+)*\s*\|\s*[A-ZÀ-Úa-zà-ú]+(?:\s+[A-ZÀ-Úa-zà-ú]+)*|Tarifa)', corpo)
        subcategoria = sub.group(1).strip() if sub else ""

        # Descrição final = contraparte + subcategoria
        desc_parts = [p for p in [contraparte, subcategoria] if p]
        desc = " ".join(desc_parts) if desc_parts else corpo[:60]

        dados.append({
            'Data':      data,
            'Descricao': padronizar_texto(desc),
            'Valor':     abs(valor_num),
            'Sinal':     sinal
        })

    return pd.DataFrame(dados)

@st.cache_data(show_spinner=False)
def extrair_texto_ofx(file_bytes):
    dados_extraidos = []
    ofx = OfxParser.parse(io.BytesIO(file_bytes))
    for account in ofx.accounts:
        for tx in account.statement.transactions:
            valor = float(tx.amount)
            dados_extraidos.append({
                'Data': tx.date.strftime('%d/%m/%Y'),
                'Descricao': padronizar_texto(tx.payee if tx.payee else tx.memo),
                'Valor': abs(valor),
                'Sinal': '+' if valor > 0 else '-'
            })
    return pd.DataFrame(dados_extraidos)

# ---------------------------------------------------------
# 3. INTERFACE PRINCIPAL E MESA POR CLIQUES
# ---------------------------------------------------------
st.set_page_config(page_title="Conciliador Pro", page_icon="🎯", layout="wide")

if 'skipped_indices' not in st.session_state:
    st.session_state.skipped_indices = []

st.title("🎯 Conciliador Pro - Mesa por Cliques")

conn = get_connection()
empresas = pd.read_sql("SELECT id, nome FROM empresas", conn)
col_cfg1, col_cfg2 = st.columns([2, 1])
empresa_sel = col_cfg1.selectbox("Empresa / Filial", empresas['nome'])
id_empresa = int(empresas[empresas['nome'] == empresa_sel]['id'].values[0])
conta_banco_fixa = col_cfg2.text_input("Conta Banco (Âncora)", value="196")

uploaded_files = st.file_uploader("Arraste seus extratos (PDF ou OFX)", type=["pdf", "ofx"], accept_multiple_files=True)

if uploaded_files and conta_banco_fixa:
    with st.spinner("Construindo recintos espaciais e processando..."):
        lista_dfs = []
        for file in uploaded_files:
            file_name = file.name.lower()
            if file_name.endswith('.pdf'):
                lista_dfs.append(extrair_por_recintos(file.getvalue()))
            elif file_name.endswith('.ofx'):
                lista_dfs.append(extrair_texto_ofx(file.getvalue()))
                
        df_bruto = pd.concat(lista_dfs, ignore_index=True)

        # --- DEBUG TEMPORÁRIO (remova quando estiver OK) ---
        with st.expander("🔍 Ver dados extraídos do PDF"):
            st.dataframe(df_bruto)
        # --- FIM DEBUG ---

        regras = pd.read_sql(f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa}", conn)

    prontos, pendentes = [], []
    
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

    # Fila Dinâmica
    df_p = pd.DataFrame(pendentes)
    if not df_p.empty:
        fila_atual = df_p[~df_p['idx_original'].isin(st.session_state.skipped_indices)]
        
        if fila_atual.empty and st.session_state.skipped_indices:
            st.session_state.skipped_indices = []
            st.rerun()
            
        st.metric("Lançamentos Pendentes", len(df_p))
        
        if not fila_atual.empty:
            item = fila_atual.iloc[0]
            st.divider()
            st.subheader("🎓 Mesa de Treinamento por Cliques")
            
            # Pílulas Interativas
            palavras = item['Descricao'].split()
            st.write("**Selecione as palavras que definem a regra:**")
            selecionadas = st.pills("Palavras", palavras, selection_mode="multi", label_visibility="collapsed")
            
            termo_final = " ".join(selecionadas) if selecionadas else ""
            st.text_input("Sua Regra será:", value=termo_final, disabled=True)
            
            if termo_final:
                impacto = df_p[df_p['Descricao'].str.contains(termo_final)]['idx_original'].count()
                st.info(f"💡 Esta regra limpa **{impacto}** lançamentos.")

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

    if not prontos:
        st.info("Aguardando processamento...")
    elif not pendentes:
        st.success("🎉 Tudo mapeado! Exportação liberada.")
        st.download_button("📥 BAIXAR CSV ALTERDATA", pd.DataFrame(prontos).to_csv(index=False, sep=';', encoding='latin1'), "importar.csv", "text/csv", type="primary")

conn.close()
