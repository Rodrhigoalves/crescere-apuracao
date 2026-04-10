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
# 2. INTELIGÊNCIA: AUTO-LEITURA ESTREITA DE CNPJ
# ---------------------------------------------------------
def identificar_empresa_no_pdf(file_bytes, df_empresas):
    """Busca APENAS por CNPJ para garantir que a Filial correta seja selecionada"""
    if 'cnpj' not in df_empresas.columns:
        return None
        
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if len(pdf.pages) > 0:
                texto = pdf.pages[0].extract_text()
                if not texto: return None
                
                t_limpo = re.sub(r'[^0-9/.\-]', '', texto)
                match = re.search(r'\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}', t_limpo)
                if match:
                    cnpj_lido = re.sub(r'[^0-9]', '', match.group(0))
                    df_empresas['cnpj_limpo'] = df_empresas['cnpj'].astype(str).apply(lambda x: re.sub(r'[^0-9]', '', x))
                    match_df = df_empresas[df_empresas['cnpj_limpo'] == cnpj_lido]
                    if not match_df.empty:
                        # Retorna a posição exata (index numérico) para o selectbox
                        return df_empresas.index.get_loc(match_df.index[0])
    except Exception:
        pass
    return None

# ---------------------------------------------------------
# 3. MOTOR DE EXTRAÇÃO PDF (BLINDADO)
# ---------------------------------------------------------
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
            
        # EXTRAÇÃO BLINDADA: Para no primeiro valor monetário. Ignora o saldo final da linha.
        match = re.search(r'(\d{2}/\d{2}/\d{2,4})\s+(Saída|Entrada|Saque|Depósito|Transferência|PIX|Tarifa)\s+(.*?)\s*(?:R\$?\s*)?(\d{1,3}(?:\.\d{3})*,\d{2})', linha, re.IGNORECASE)
        
        if match:
            data = match.group(1)
            tipo = match.group(2)
            desc_limpa = match.group(3).strip() # O texto limpo antes do primeiro valor
            valor_num = float(match.group(4).replace('.', '').replace(',', '.'))
            sinal = '+' if tipo.lower() in ['entrada', 'depósito'] else '-'
            
            if not desc_limpa or len(desc_limpa) < 2:
                desc_limpa = tipo.upper()
                
            dados.append({'Data': data, 'Descricao': padronizar_texto(desc_limpa), 'Valor': abs(valor_num), 'Sinal': sinal})
        elif len(linha) > 8: 
            ignoradas_raw.append(linha)

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
# 4. CARGA DE DADOS DO BANCO
# ---------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def carregar_empresas():
    conn = get_connection()
    # Puxa as colunas exatas que estão no seu banco de dados
    query = "SELECT id, nome, cnpj, tipo, apelido_unidade, conta_contabil FROM empresas"
    df = pd.read_sql(query, conn)
    
    # Cria uma coluna visual para o selectbox (Ex: LARAMIX | Matriz | 29.213...)
    df['tipo'] = df['tipo'].fillna('Matriz')
    df['cnpj'] = df['cnpj'].fillna('Sem CNPJ')
    df['display_nome'] = df['nome'] + ' | ' + df['tipo'] + ' | ' + df['cnpj']
    
    conn.close()
    return df

# ---------------------------------------------------------
# 5. INTERFACE PRINCIPAL
# ---------------------------------------------------------
st.set_page_config(page_title="Conciliação Bancária", page_icon="🏦", layout="wide")

# VARIÁVEIS DE SESSÃO
if 'skipped_indices' not in st.session_state: st.session_state.skipped_indices = []
if 'editando_regra_id' not in st.session_state: st.session_state.editando_regra_id = None
if 'historico_acoes' not in st.session_state: st.session_state.historico_acoes = [] # Controle do Desfazer

st.title("🏦 Conciliação Bancária")

df_empresas = carregar_empresas()

# 1º PASSO: UPLOAD DOS ARQUIVOS
uploaded_files = st.file_uploader("1. Arraste seus extratos (PDF ou OFX)", type=["pdf", "ofx"], accept_multiple_files=True)

# 2º PASSO: INTELIGÊNCIA DE PRÉ-SELEÇÃO
indice_sugerido = 0
if uploaded_files:
    for file in uploaded_files:
        if file.name.lower().endswith('.pdf'):
            idx_encontrado = identificar_empresa_no_pdf(file.getvalue(), df_empresas)
            if idx_encontrado is not None:
                indice_sugerido = int(idx_encontrado)
                st.toast(f"✅ Extrato da empresa {df_empresas.iloc[indice_sugerido]['nome']} reconhecido!")
                break

# 3º PASSO: PAINEL DE CONFIGURAÇÕES
st.markdown("### 2. Confirme os Dados")
col_cfg1, col_cfg2, col_cfg3 = st.columns([2, 1, 1])

# Selectbox usa o nome formatado para evitar confusão entre Matriz/Filial
empresa_sel_display = col_cfg1.selectbox("Empresa / Filial", df_empresas['display_nome'], index=indice_sugerido)
empresa_data = df_empresas[df_empresas['display_nome'] == empresa_sel_display].iloc[0]
id_empresa = int(empresa_data['id'])

conta_sugerida = "196"
if pd.notna(empresa_data['conta_contabil']) and str(empresa_data['conta_contabil']).strip():
    conta_sugerida = str(empresa_data['conta_contabil'])

conta_banco_fixa = col_cfg2.text_input("Conta Banco (Âncora)", value=conta_sugerida)
saldo_anterior_informado = col_cfg3.number_input("Saldo Anterior (R$)", value=0.00, step=100.00, format="%.2f")

# 4º PASSO: PROCESSAMENTO REAL
if uploaded_files and conta_banco_fixa:
    with st.spinner("Lendo e classificando extratos..."):
        lista_dfs, criticas, comuns = [], [], []
        for file in uploaded_files:
            if file.name.lower().endswith('.pdf'):
                df_ex, ign = extrair_por_recintos(file.getvalue())
                lista_dfs.append(df_ex)
                criticas.extend(ign['criticas']); comuns.extend(ign['comuns'])
            else: 
                lista_dfs.append(extrair_texto_ofx(file.getvalue()))

        df_bruto = pd.concat(lista_dfs, ignore_index=True) if lista_dfs else pd.DataFrame()
        conn = get_connection()
        regras = pd.read_sql(f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa}", conn)
        conn.close()

    # --- CLASSIFICAÇÃO E CÁLCULO DINÂMICO DE SALDO ---
    prontos, pendentes = [], []
    linhas_ignoradas_regras = [] 

    if not df_bruto.empty:
        for idx, row in df_bruto.iterrows():
            match = False
            for _, r in regras.iterrows():
                if fuzz.partial_ratio(padronizar_texto(r['termo_chave']), row['Descricao']) >= 85 and r['sinal_esperado'] == row['Sinal']:
                    if r['conta_contabil'] == 'IGNORAR':
                        linhas_ignoradas_regras.append(idx)
                    else:
                        d = conta_banco_fixa if row['Sinal'] == '+' else r['conta_contabil']
                        c = r['conta_contabil'] if row['Sinal'] == '+' else conta_banco_fixa
                        prontos.append({'Debito': d, 'Credito': c, 'Data': row['Data'], 'Valor': f"{row['Valor']:.2f}".replace('.', ','), 'Historico': r['historico_padrao'] or row['Descricao']})
                    match = True; break
            if not match: pendentes.append({'idx_original': idx, **row})

    # --- AUDITORIA DE SALDOS (DINÂMICO) ---
    st.divider()
    if not df_bruto.empty:
        df_validos = df_bruto[~df_bruto.index.isin(linhas_ignoradas_regras)]
        total_e = df_validos[df_validos['Sinal'] == '+']['Valor'].sum()
        total_s = df_validos[df_validos['Sinal'] == '-']['Valor'].sum()
        saldo_final_calculado = saldo_anterior_informado + total_e - total_s

        c_aud1, c_aud2, c_aud3, c_aud4 = st.columns(4)
        c_aud1.metric("Saldo Anterior", formatar_moeda(saldo_anterior_informado))
        c_aud2.metric("🟢 Entradas Válidas", formatar_moeda(total_e))
        c_aud3.metric("🔴 Saídas Válidas", formatar_moeda(total_s))
        c_aud4.metric("⚖️ Saldo Final Calculado", formatar_moeda(saldo_final_calculado))

    if criticas or comuns:
        with st.expander(f"⚠️ Alertas de Leitura de PDF ({len(criticas)} críticas / {len(comuns)} informativas)"):
            if criticas:
                st.error("Linhas com valores suspeitos (possíveis transações não lidas):")
                for l in list(dict.fromkeys(criticas)): st.code(l)
            if comuns:
                st.info("Ruídos e textos informativos ignorados:")
                for l in list(dict.fromkeys(comuns))[:20]: st.text(l)

    # --- MESA DE TREINAMENTO ---
    df_p = pd.DataFrame(pendentes)
    if not df_p.empty:
        fila = df_p[~df_p['idx_original'].isin(st.session_state.skipped_indices)]
        if not fila.empty:
            item = fila.iloc[0]
            st.subheader("🎓 Mesa de Treinamento")
            
            # CABEÇALHO DO ITEM + BOTÃO DESFAZER
            m1, m2, m3, m4, m5 = st.columns([1, 1, 1, 3, 1])
            m1.metric("📅 Data", item['Data'])
            m2.metric("💰 Valor", formatar_moeda(item['Valor']))
            m3.metric("↕️ Tipo", "🟢 Entrada" if item['Sinal'] == '+' else "🔴 Saída")
            m4.write(f"**Descrição Extraída:** {item['Descricao']}")
            
            # Lógica do Botão Desfazer
            if st.session_state.historico_acoes:
                if m5.button("↩️ Desfazer Ação", help="Desfaz a última regra salva, lixo ignorado ou pulo"):
                    ultima_acao = st.session_state.historico_acoes.pop()
                    if ultima_acao['tipo'] in ['salvar', 'ignorar']:
                        conn = get_connection(); cursor = conn.cursor()
                        cursor.execute("DELETE FROM tb_extratos_regras WHERE id = %s", (ultima_acao['id'],))
                        conn.commit(); conn.close()
                    elif ultima_acao['tipo'] == 'pular':
                        if ultima_acao['idx'] in st.session_state.skipped_indices:
                            st.session_state.skipped_indices.remove(ultima_acao['idx'])
                    st.rerun()

            palavras_desc = item['Descricao'].split()
            selecionadas = st.pills("Selecione os termos-chave (ou deixe vazio para a frase inteira):", palavras_desc, selection_mode="multi")
            termo_final = " ".join(selecionadas) if selecionadas else item['Descricao']
            
            # --- RESTAURADO: LANÇAMENTOS IMPACTADOS ---
            if termo_final:
                # Usa case=False para garantir que ache independente de maiúsculas/minúsculas
                df_impactados = df_p[df_p['Descricao'].str.contains(re.escape(termo_final), case=False, na=False)]
                impacto = len(df_impactados)
                if impacto > 0:
                    st.info(f"💡 Esta regra resolverá **{impacto}** lançamentos desta fila.")
                    with st.expander(f"📋 Ver lançamentos impactados ({impacto})", expanded=False):
                        st.dataframe(
                            df_impactados[['Data', 'Descricao', 'Valor', 'Sinal']].reset_index(drop=True),
                            use_container_width=True
                        )
            # ------------------------------------------

            with st.form("form_treino"):
                st.caption(f"A regra atuará sobre: **{termo_final}**")
                f1, f2, f3 = st.columns(3)
                contra = f1.text_input("Contrapartida")
                cod_h, txt_h = f2.text_input("Cód. Hist."), f3.text_input("Histórico Padrão")
                
                b1, b2, b3, b4 = st.columns(4)
                if b1.form_submit_button("✅ Salvar Regra"):
                    if contra:
                        conn = get_connection(); cursor = conn.cursor()
                        cursor.execute("INSERT INTO tb_extratos_regras (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil, cod_historico_erp, historico_padrao) VALUES (%s, %s, %s, %s, %s, %s, %s)", (id_empresa, 'PADRAO', termo_final, item['Sinal'], contra, cod_h, txt_h))
                        id_inserido = cursor.lastrowid
                        conn.commit(); conn.close()
                        st.session_state.historico_acoes.append({'tipo': 'salvar', 'id': id_inserido})
                        st.rerun()
                    else: st.error("Preencha a conta de contrapartida para salvar.")
                        
                if b2.form_submit_button("🗑️ Ignorar Lixo"):
                    conn = get_connection(); cursor = conn.cursor()
                    cursor.execute("INSERT INTO tb_extratos_regras (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil) VALUES (%s, %s, %s, %s, %s)", (id_empresa, 'PADRAO', termo_final, item['Sinal'], 'IGNORAR'))
                    id_inserido = cursor.lastrowid
                    conn.commit(); conn.close()
                    st.session_state.historico_acoes.append({'tipo': 'ignorar', 'id': id_inserido})
                    st.rerun()
                    
                if b3.form_submit_button("⏭️ Pular"): 
                    st.session_state.skipped_indices.append(item['idx_original'])
                    st.session_state.historico_acoes.append({'tipo': 'pular', 'idx': item['idx_original']})
                    st.rerun()
                    
                if b4.form_submit_button("🔄 Resetar Fila"): 
                    st.session_state.skipped_indices = []
                    st.session_state.historico_acoes = []
                    st.rerun()

    if prontos and not pendentes:
        st.success("🎉 Todos os lançamentos foram mapeados! Exportação liberada.")
        st.download_button("📥 BAIXAR CSV ALTERDATA", pd.DataFrame(prontos).to_csv(index=False, sep=';', encoding='latin1'), "importar.csv", "text/csv")

# --- GERENCIAMENTO DE REGRAS ---
st.divider()
with st.expander("📚 Gerenciar Regras Cadastradas", expanded=False):
    conn = get_connection()
    regras_v = pd.read_sql(f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa} ORDER BY id DESC", conn)
    
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
        st.info("Nenhuma regra encontrada na base de dados.")
    conn.close()
