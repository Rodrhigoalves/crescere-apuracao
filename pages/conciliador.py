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
        database=st.secrets["mysql"]["database"],
        use_pure=True,      
        ssl_disabled=True   
    )

def padronizar_texto(texto):
    if not texto: return ""
    texto_sem_acento = unicodedata.normalize('NFKD', str(texto)).encode('ASCII', 'ignore').decode('utf-8')
    texto_limpo = re.sub(r'\s+', ' ', texto_sem_acento.upper().strip())
    return texto_limpo

def formatar_moeda(valor):
    return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

# ---------------------------------------------------------
# 2. MOTOR DE EXTRAÇÃO PDF
# ---------------------------------------------------------
def _extrair_nome_final(chunk: str) -> str:
    texto = re.sub(r'\d{1,3}(?:\.\d{3})*,\d{2}', '', chunk)
    texto = re.sub(r'R\$|\bS\.A\b\.?', '', texto)
    texto = re.sub(r'[|\-]', ' ', texto)
    texto = re.sub(r'\s+', ' ', texto).strip()

    RUIDO_TOKENS = {'parcela', 'emprestimo', 'transferencia', 'pix', 'maquininha', 'debito', 'credito', 'tarifa', 'maestro', 'elo', 'visa', 'mastercard', 'stone', 'instituicao', 'pagamento', 'sa', 'saque', 'deposito', 'ted', 'doc', 'boleto', 'cartao'}

    tokens = texto.strip().split()
    nome_tokens = []
    for tok in reversed(tokens):
        tok_norm = unicodedata.normalize('NFKD', tok).encode('ASCII', 'ignore').decode().lower()
        if len(tok_norm) < 2 or tok_norm in RUIDO_TOKENS:
            if nome_tokens: break
            continue        
        if re.match(r'^[A-Za-záéíóúâêîôûãõçàÁÉÍÓÚÂÊÎÔÛÃÕÇÀ]{2,}$', tok):
            nome_tokens.insert(0, tok)
        else:
            if nome_tokens: break
    return " ".join(nome_tokens)

@st.cache_data(show_spinner=False)
def extrair_por_recintos(file_bytes):
    texto_completo = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            palavras = page.extract_words(x_tolerance=3, y_tolerance=3)
            linhas_dict = {}
            for p in palavras:
                y = round(float(p['top']))
                encontrou_y = next((k for k in linhas_dict if abs(k - y) <= 3), None)
                if encontrou_y is not None: linhas_dict[encontrou_y].append(p)
                else: linhas_dict[y] = [p]

            for y_key in sorted(linhas_dict.keys()):
                linha_ordenada = sorted(linhas_dict[y_key], key=lambda x: x['x0'])
                texto_completo += " ".join([w['text'] for w in linha_ordenada]) + "\n"

    RUIDO_CABECALHO = ["período:", "página", "saldo anterior", "saldo atual", "saldo final", "cnpj", "emitido em", "extrato de conta", "dados da conta", "nome documento", "instituição agência", "contraparte stone"]
             
    linhas = [l.strip() for l in texto_completo.split('\n') if l.strip()]
    dados, ignoradas_raw = [], []

    for linha in linhas:
        if any(x in linha.lower() for x in RUIDO_CABECALHO): continue
            
        match = re.search(r'(\d{2}/\d{2}/\d{2,4})\s+(Saída|Entrada|Saque|Depósito|Transferência|PIX)', linha, re.IGNORECASE)
        if match:
            data, tipo = match.group(1), match.group(2)
            valores = re.findall(r'\d{1,3}(?:\.\d{3})*,\d{2}', linha)
            if valores:
                valor_num = float(valores[0].replace('.', '').replace(',', '.'))
                sinal = '+' if tipo.lower() in ['entrada', 'depósito'] else '-'
                desc_limpa = linha.replace(data, "").replace(valores[0], "").strip()
                dados.append({'Data': data, 'Descricao': padronizar_texto(desc_limpa), 'Valor': abs(valor_num), 'Sinal': sinal})
            else: ignoradas_raw.append(linha)
        elif len(linha) > 8: ignoradas_raw.append(linha)

    # Filtro Pente-fino nos ignorados
    ignoradas_unicas = list(dict.fromkeys(ignoradas_raw))
    ignoradas_com_valor = [l for l in ignoradas_unicas if re.search(r'\d,\d{2}', l)]
    ignoradas_texto = [l for l in ignoradas_unicas if not re.search(r'\d,\d{2}', l)]

    return pd.DataFrame(dados), {"criticas": ignoradas_com_valor, "comuns": ignoradas_texto}

@st.cache_data(show_spinner=False)
def extrair_texto_ofx(file_bytes):
    dados_extraidos = []
    ofx = OfxParser.parse(io.BytesIO(file_bytes))
    for account in ofx.accounts:
        for tx in account.statement.transactions:
            valor = float(tx.amount)
            dados_extraidos.append({'Data': tx.date.strftime('%d/%m/%Y'), 'Descricao': padronizar_texto(tx.payee if tx.payee else tx.memo), 'Valor': abs(valor), 'Sinal': '+' if valor > 0 else '-'})
    return pd.DataFrame(dados_extraidos)

# ---------------------------------------------------------
# 3. INTERFACE PRINCIPAL
# ---------------------------------------------------------
st.set_page_config(page_title="Conciliação Bancária", page_icon="🏦", layout="wide")

if 'skipped_indices' not in st.session_state: st.session_state.skipped_indices = []
if 'editando_regra_id' not in st.session_state: st.session_state.editando_regra_id = None

st.title("🏦 Conciliação Bancária")

conn = get_connection()

# Ajustado para carregar apenas colunas existentes
df_empresas = pd.read_sql("SELECT id, nome FROM empresas", conn)

col_cfg1, col_cfg2, col_cfg3 = st.columns([2, 1, 1])
empresa_sel = col_cfg1.selectbox("Empresa / Filial", df_empresas['nome'])
id_empresa = int(df_empresas[df_empresas['nome'] == empresa_sel]['id'].values[0])

# Como a coluna não existe no banco, mantemos o padrão 196 manual
conta_banco_fixa = col_cfg2.text_input("Conta Banco (Âncora)", value="196")
saldo_anterior_informado = col_cfg3.number_input("Saldo Anterior (R$)", value=0.00, step=100.00, format="%.2f")

uploaded_files = st.file_uploader("Arraste seus extratos (PDF ou OFX)", type=["pdf", "ofx"], accept_multiple_files=True)

if uploaded_files and conta_banco_fixa:
    with st.spinner("Processando..."):
        lista_dfs, criticas, comuns = [], [], []
        for file in uploaded_files:
            if file.name.lower().endswith('.pdf'):
                df_ex, ign = extrair_por_recintos(file.getvalue())
                lista_dfs.append(df_ex)
                criticas.extend(ign['criticas']); comuns.extend(ign['comuns'])
            else: lista_dfs.append(extrair_texto_ofx(file.getvalue()))

        df_bruto = pd.concat(lista_dfs, ignore_index=True) if lista_dfs else pd.DataFrame()
        regras = pd.read_sql(f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa}", conn)

    # --- AUDITORIA ---
    st.divider()
    if not df_bruto.empty:
        total_e = df_bruto[df_bruto['Sinal'] == '+']['Valor'].sum()
        total_s = df_bruto[df_bruto['Sinal'] == '-']['Valor'].sum()
        c_aud1, c_aud2, c_aud3, c_aud4 = st.columns(4)
        c_aud1.metric("Saldo Anterior", formatar_moeda(saldo_anterior_informado))
        c_aud2.metric("🟢 Entradas", formatar_moeda(total_e))
        c_aud3.metric("🔴 Saídas", formatar_moeda(total_s))
        c_aud4.metric("⚖️ Saldo Final", formatar_moeda(saldo_anterior_informado + total_e - total_s))

    if criticas or comuns:
        with st.expander(f"⚠️ Alertas de Leitura ({len(criticas)} críticas / {len(comuns)} informativas)"):
            if criticas:
                st.error("Linhas com valores suspeitos (possíveis transações não lidas):")
                for l in list(dict.fromkeys(criticas)): st.code(l)
            if comuns:
                st.info("Ruídos e textos informativos ignorados (Top 20 únicos):")
                for l in list(dict.fromkeys(comuns))[:20]: st.text(l)

    # --- CLASSIFICAÇÃO ---
    prontos, pendentes = [], []
    for idx, row in df_bruto.iterrows():
        match = False
        for _, r in regras.iterrows():
            if fuzz.partial_ratio(padronizar_texto(r['termo_chave']), row['Descricao']) >= 85 and r['sinal_esperado'] == row['Sinal']:
                if r['conta_contabil'] != 'IGNORAR':
                    d = conta_banco_fixa if row['Sinal'] == '+' else r['conta_contabil']
                    c = r['conta_contabil'] if row['Sinal'] == '+' else conta_banco_fixa
                    prontos.append({'Debito': d, 'Credito': c, 'Data': row['Data'], 'Valor': f"{row['Valor']:.2f}".replace('.', ','), 'Historico': r['historico_padrao'] or row['Descricao']})
                match = True; break
        if not match: pendentes.append({'idx_original': idx, **row})

    # --- MESA DE TREINAMENTO ---
    df_p = pd.DataFrame(pendentes)
    if not df_p.empty:
        fila = df_p[~df_p['idx_original'].isin(st.session_state.skipped_indices)]
        if not fila.empty:
            item = fila.iloc[0]
            st.subheader("🎓 Mesa de Treinamento")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("📅 Data", item['Data']); m2.metric("💰 Valor", formatar_moeda(item['Valor']))
            m3.metric("↕️ Tipo", "🟢 Entrada" if item['Sinal'] == '+' else "🔴 Saída")
            m4.write(f"**Descrição:** {item['Descricao']}")
            
            selecionadas = st.pills("Selecione os termos da regra:", item['Descricao'].split(), selection_mode="multi")
            termo_final = " ".join(selecionadas) if selecionadas else ""
            
            with st.form("form_treino"):
                f1, f2, f3 = st.columns(3)
                contra = f1.text_input("Contrapartida")
                cod_h, txt_h = f2.text_input("Cód. Hist."), f3.text_input("Histórico Padrão")
                b1, b2, b3, b4 = st.columns(4)
                if b1.form_submit_button("✅ Salvar"):
                    if termo_final and contra:
                        cursor = conn.cursor()
                        cursor.execute("INSERT INTO tb_extratos_regras (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil, cod_historico_erp, historico_padrao) VALUES (%s, %s, %s, %s, %s, %s, %s)", (id_empresa, 'PADRAO', termo_final, item['Sinal'], contra, cod_h, txt_h))
                        conn.commit(); st.rerun()
                if b2.form_submit_button("🗑️ Ignorar"):
                    if termo_final:
                        cursor = conn.cursor()
                        cursor.execute("INSERT INTO tb_extratos_regras (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil) VALUES (%s, %s, %s, %s, %s)", (id_empresa, 'PADRAO', termo_final, item['Sinal'], 'IGNORAR'))
                        conn.commit(); st.rerun()
                if b3.form_submit_button("⏭️ Pular"): st.session_state.skipped_indices.append(item['idx_original']); st.rerun()
                if b4.form_submit_button("🔄 Reset"): st.session_state.skipped_indices = []; st.rerun()

    if prontos and not pendentes:
        st.success("🎉 Processamento concluído!")
        st.download_button("📥 BAIXAR CSV ALTERDATA", pd.DataFrame(prontos).to_csv(index=False, sep=';', encoding='latin1'), "importar.csv", "text/csv")

# --- GERENCIAMENTO DE REGRAS (RETRACTABLE) ---
st.divider()
with st.expander("📚 Gerenciar Regras Cadastradas", expanded=False):
    regras_v = pd.read_sql(f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa} ORDER BY id DESC", conn)
    
    # Campo de busca dentro do expander
    busca = st.text_input("🔍 Buscar por termo chave ou conta contábil...")
    if busca:
        regras_v = regras_v[
            regras_v['termo_chave'].str.contains(busca, case=False, na=False) | 
            regras_v['conta_contabil'].str.contains(busca, case=False, na=False)
        ]
    
    if not regras_v.empty:
        for _, r in regras_v.iterrows():
            col_r = st.columns([3, 2, 1, 1, 1])
            col_r[0].write(f"**{r['termo_chave']}**")
            col_r[1].write(f"Conta: {r['conta_contabil']} ({r['sinal_esperado']})")
            if col_r[2].button("✏️", key=f"e_{r['id']}"):
                st.session_state.editando_regra_id = r['id']; st.rerun()
            if col_r[3].button("🗑️", key=f"d_{r['id']}"):
                cursor = conn.cursor(); cursor.execute("DELETE FROM tb_extratos_regras WHERE id = %s", (int(r['id']),))
                conn.commit(); st.rerun()
    else:
        st.info("Nenhuma regra encontrada para os critérios informados.")

conn.close()
