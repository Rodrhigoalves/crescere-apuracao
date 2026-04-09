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
# 2. MOTOR DE RECINTOS (ÂNCORA DE MARGEM ESQUERDA)
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
    data_memoria = ""
    
    for linha in linhas:
        linha = linha.strip()
        
        # Filtro Anti-Ruído: Ignora cabeçalhos e rodapés inúteis
        if not linha or any(x in linha.lower() for x in ["período:", "página", "saldo anterior", "saldo atual", "saldo final", "data tipo descri", "cnpj"]):
            continue
            
        # O GATILHO INFALÍVEL: A linha começa com uma Data ou Hora na margem esquerda?
        match_inicio = re.match(r'^(\d{2}/\d{2}/\d{2,4}|\d{2}:\d{2})', linha)
        
        if match_inicio:
            # Se achou uma nova data/hora, empacota o bloco anterior inteiro e guarda
            if bloco_atual:
                blocos.append(bloco_atual)
            
            # Atualiza a memória da data (se for só hora, ele usa a data do dia)
            marcador = match_inicio.group(1)
            if "/" in marcador:
                data_memoria = marcador
                
            # Cria a nova caixa blindada
            bloco_atual = {
                'Data': data_memoria,
                'linhas_texto': [linha]
            }
        else:
            # Se não começou com data/hora, é texto flutuante. Pertence à caixa atual.
            if bloco_atual:
                bloco_atual['linhas_texto'].append(linha)
                
    if bloco_atual: 
        blocos.append(bloco_atual)
        
    # Fase 2: Processar as caixas fechadas
    dados_extraidos = []
    for b in blocos:
        texto_full = " ".join(b['linhas_texto'])
        valores = re.findall(r'\b\d{1,3}(?:\.\d{3})*,\d{2}\b', texto_full)
        
        # Se o bloco não tem nenhum valor monetário, descarta (era sujeira de PDF)
        if not valores:
            continue
            
        valor_num = float(valores[0].replace('.', '').replace(',', '.'))
        sinal = '+' if 'Entrada' in texto_full else '-' if 'Saída' in texto_full else '-' if ' - R$' in texto_full else '+'
        
        # Limpeza cirúrgica do texto para a Mesa de Treinamento
        desc_limpa = texto_full
        # Arranca a data/hora do começo
        desc_limpa = re.sub(r'^(\d{2}/\d{2}/\d{2,4}|\d{2}:\d{2})', '', desc_limpa).strip()
        # Arranca os valores e sinais monetários
        for v in valores:
            desc_limpa = desc_limpa.replace(v, '')
        desc_limpa = re.sub(r'\b(Entrada|Saída|R\$|-)\b', '', desc_limpa, flags=re.IGNORECASE).strip()
        
        dados_extraidos.append({
            'Data': b['Data'],
            'Descricao': padronizar_texto(desc_limpa),
            'Valor': abs(valor_num),
            'Sinal': sinal
        })
        
    return pd.DataFrame(dados_extraidos)

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
