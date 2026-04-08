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
# 1. CONEXÃO E NORMALIZAÇÃO
# ---------------------------------------------------------
def get_connection():
    return mysql.connector.connect(
        host=st.secrets["mysql"]["host"],
        user=st.secrets["mysql"]["user"],
        password=st.secrets["mysql"]["password"],
        database=st.secrets["mysql"]["database"]
    )

def padronizar_texto(texto):
    """Remove acentos, deixa tudo maiúsculo e tira espaços duplos."""
    if not texto: return ""
    texto_sem_acento = unicodedata.normalize('NFKD', str(texto)).encode('ASCII', 'ignore').decode('utf-8')
    texto_limpo = re.sub(r'\s+', ' ', texto_sem_acento.upper().strip())
    return texto_limpo

# ---------------------------------------------------------
# 2. MOTORES DE LEITURA (PDF E OFX)
# ---------------------------------------------------------
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
        
        tem_data = re.search(r'(\d{2}/\d{2}/\d{2,4})', linha)
        tem_hora = re.search(r'(\d{2}:\d{2})', linha)
        
        if tem_data or tem_hora:
            if transacao_atual: dados_extraidos.append(transacao_atual)
            
            data = tem_data.group(1) if tem_data else (transacao_atual['Data'] if transacao_atual else "")
            sinal = '+' if 'Entrada' in linha else '-' if 'Saída' in linha else '-' if ' - R$' in linha else '+'
            
            valores = re.findall(r'\d{1,3}(?:\.\d{3})*,\d{2}', linha)
            valor_num = float(valores[0].replace('.', '').replace(',', '.')) if valores else 0.0
            
            desc = linha
            if tem_data: desc = desc.replace(tem_data.group(0), '')
            if tem_hora: desc = desc.replace(tem_hora.group(0), '')
            for v in valores: desc = desc.replace(v, '')
            desc = desc.replace('Entrada', '').replace('Saída', '').replace('R$', '').replace('-', '').strip()
            
            transacao_atual = {'Data': data, 'Descricao': padronizar_texto(desc), 'Valor': abs(valor_num), 'Sinal': sinal}
        else:
            if transacao_atual and len(linha) > 2 and "Saldo" not in linha and "Página" not in linha:
                complemento = linha.replace('R$', '').strip()
                transacao_atual['Descricao'] += f" {padronizar_texto(complemento)}"
                transacao_atual['Descricao'] = re.sub(r'\s+', ' ', transacao_atual['Descricao'])
                
    if transacao_atual: dados_extraidos.append(transacao_atual)
    return pd.DataFrame(dados_extraidos)

@st.cache_data(show_spinner=False)
def extrair_texto_ofx(file_bytes):
    """Leitor nativo de OFX. 100% de precisão nos dados bancários."""
    dados_extraidos = []
    ofx = OfxParser.parse(io.BytesIO(file_bytes))
    
    for account in ofx.accounts:
        for tx in account.statement.transactions:
            valor = float(tx.amount)
            sinal = '+' if valor > 0 else '-'
            desc_original = tx.payee if tx.payee else tx.memo
            
            dados_extraidos.append({
                'Data': tx.date.strftime('%d/%m/%Y'),
                'Descricao': padronizar_texto(desc_original),
                'Valor': abs(valor),
                'Sinal': sinal
            })
    return pd.DataFrame(dados_extraidos)

# ---------------------------------------------------------
# 3. INTERFACE PRINCIPAL
# ---------------------------------------------------------
st.set_page_config(page_title="Conciliador", page_icon="🎯", layout="wide")
st.title("🎯 Conciliador de Extratos Pro (Fuzzy AI)")

col_cfg1, col_cfg2 = st.columns([2, 1])
try:
    # CONEXÃO 1: Apenas para carregar as empresas. Abre e fecha rápido.
    conn_setup = get_connection()
    empresas = pd.read_sql("SELECT id, nome FROM empresas", conn_setup)
    conn_setup.close()
    
    empresa_sel = col_cfg1.selectbox("Empresa / Filial", empresas['nome'])
    id_empresa = int(empresas[empresas['nome'] == empresa_sel]['id'].values[0])
    conta_banco_fixa = col_cfg2.text_input("Conta Contábil do Banco (Âncora)", value="196")
except Exception as e:
    st.error(f"Erro ao carregar dados iniciais: {e}")
    st.stop()

uploaded_files = st.file_uploader("Suba os arquivos (PDF ou OFX)", type=["pdf", "ofx"], accept_multiple_files=True)

if uploaded_files and conta_banco_fixa:
    with st.spinner("Lendo arquivos e calculando similaridade neural..."):
        
        lista_dfs = []
        for file in uploaded_files:
            file_name = file.name.lower()
            if file_name.endswith('.pdf'):
                lista_dfs.append(extrair_texto_pdf(file.getvalue()))
            elif file_name.endswith('.ofx'):
                lista_dfs.append(extrair_texto_ofx(file.getvalue()))
        
        df_bruto = pd.concat(lista_dfs, ignore_index=True)
        
        # CONEXÃO 2: O arquivo já foi processado. Agora abrimos para buscar as regras.
        conn_regras = get_connection()
        regras = pd.read_sql(f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa}", conn_regras)
        conn_regras.close()

    prontos_alterdata = []
    pendentes_treinamento = []

    # Motor de Classificação com Inteligência FUZZY
    for _, row in df_bruto.iterrows():
        match_encontrado = False
        ignorar_linha = False
        
        for _, r in regras.iterrows():
            termo_regra = padronizar_texto(r['termo_chave'])
            similaridade = fuzz.partial_ratio(termo_regra, row['Descricao'])
            
            if similaridade >= 85 and r['sinal_esperado'] == row['Sinal']:
                if r['conta_contabil'] == 'IGNORAR':
                    ignorar_linha = True
                    break
                
                conta_contra = r['conta_contabil']
                debito = conta_banco_fixa if row['Sinal'] == '+' else conta_contra
                credito = conta_contra if row['Sinal'] == '+' else conta_banco_fixa
                
                cod_h = str(int(r['cod_historico_erp'])) if pd.notna(r['cod_historico_erp']) else ""
                txt_h = r['historico_padrao'] if pd.notna(r['historico_padrao']) and str(r['historico_padrao']).strip() != "" else row['Descricao']

                prontos_alterdata.append({
                    'Debito': debito, 'Credito': credito, 'Data': row['Data'],
                    'Valor': f"{row['Valor']:.2f}".replace('.', ','),
                    'Cod. Historico': cod_h, 'Historico': txt_h,
                    'Nr. Documento': '', 'Cod. Centro Custo': '', 'Livro': '', 'Filial': '', 'Tipo': ''
                })
                match_encontrado = True
                break
        
        if not match_encontrado and not ignorar_linha:
            pendentes_treinamento.append(row)

    # ---------------------------------------------------------
    # 4. PAINEL DE RESULTADOS E TRAVA DE EXPORTAÇÃO
    # ---------------------------------------------------------
    df_prontos = pd.DataFrame(prontos_alterdata)
    df_pendentes = pd.DataFrame(pendentes_treinamento)

    colA, colB = st.columns(2)
    colA.success(f"✅ Prontos para ERP: {len(df_prontos)} linhas")
    colB.error(f"⚠️ Pendentes: {len(df_pendentes)} linhas")

    if df_pendentes.empty and not df_prontos.empty:
        st.balloons()
        st.success("🎉 Arquivo limpo e validado! Exportação liberada.")
        csv_data = df_prontos.to_csv(index=False, sep=';', encoding='latin1')
        st.download_button(label="📥 BAIXAR CSV ALTERDATA", data=csv_data, file_name=f"importar_{empresa_sel}.csv", mime="text/csv", type="primary", use_container_width=True)
    elif not df_pendentes.empty:
        st.warning(f"🔒 Resolva os {len(df_pendentes)} lançamentos abaixo para liberar a exportação.")

    # ---------------------------------------------------------
    # 5. MESA DE TREINAMENTO INTELIGENTE
    # ---------------------------------------------------------
    if not df_pendentes.empty:
        st.divider()
        pendentes_agrupados = df_pendentes.groupby(['Descricao', 'Sinal']).size().reset_index(name='Qtd').sort_values('Qtd', ascending=False)
        top = pendentes_agrupados.iloc[0]
        
        st.subheader(f"🎓 Treinando: {top['Qtd']} operações agrupadas")
        
        with st.form("form_treinamento", clear_on_submit=True):
            termo = st.text_input("Termo Chave (Corte a sujeira)", value=top['Descricao'])
            st.caption(f"Operação lida como **{'🟢 ENTRADA' if top['Sinal'] == '+' else '🔴 SAÍDA'}**. Conta Fixa: {conta_banco_fixa}")
            
            c1, c2, c3 = st.columns([1, 1, 2])
            conta = c1.text_input("Conta Contrapartida")
            cod_hist = c2.text_input("Cód. Hist.")
            txt_hist = c3.text_input("Texto do Histórico (Opcional)")
            
            b1, b2 = st.columns(2)
            submit_salvar = b1.form_submit_button("✅ Salvar Regra (Aplica com 85% de similaridade)")
            submit_lixo = b2.form_submit_button("🗑️ Ignorar / Descartar Lixo")
            
            if (submit_salvar and conta) or submit_lixo:
                conta_final = 'IGNORAR' if submit_lixo else conta
                termo_para_banco = padronizar_texto(termo)
                
                # CONEXÃO 3: Abre só para salvar a regra e já fecha
                conn_save = get_connection()
                cursor = conn_save.cursor()
                cursor.execute("""INSERT INTO tb_extratos_regras 
                               (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil, cod_historico_erp, historico_padrao) 
                               VALUES (%s, %s, %s, %s, %s, %s, %s)""", 
                               (id_empresa, 'PADRAO', termo_para_banco, top['Sinal'], conta_final, cod_hist if cod_hist else None, txt_hist))
                conn_save.commit()
                conn_save.close()
                st.rerun()

    # ---------------------------------------------------------
    # 6. GERENCIADOR DE REGRAS
    # ---------------------------------------------------------
    st.divider()
    with st.expander("⚙️ Gerenciar Banco de Inteligência (Ver, Editar ou Excluir)"):
        # CONEXÃO 4: Abre só para popular a tabela do gerenciador
        conn_view = get_connection()
        regras_view = pd.read_sql(f"SELECT id, termo_chave, sinal_esperado, conta_contabil as contrapartida, historico_padrao FROM tb_extratos_regras WHERE id_empresa = {id_empresa}", conn_view)
        conn_view.close()
        
        st.dataframe(regras_view, use_container_width=True)
        
        c_ed1, c_ed2 = st.columns(2)
        with c_ed1:
            st.write("**Editar Conta**")
            with st.form("form_edit"):
                id_edit = st.number_input("ID da Regra", step=1, value=0)
                nova_conta = st.text_input("Nova Conta Contrapartida")
                if st.form_submit_button("Atualizar Conta"):
                    conn_edit = get_connection()
                    cursor = conn_edit.cursor()
                    cursor.execute(f"UPDATE tb_extratos_regras SET conta_contabil = '{nova_conta}' WHERE id = {id_edit} AND id_empresa = {id_empresa}")
                    conn_edit.commit()
                    conn_edit.close()
                    st.success("Conta atualizada!"); st.rerun()
                    
        with c_ed2:
            st.write("**Excluir Regra**")
            with st.form("form_del"):
                id_del = st.number_input("ID para Excluir", step=1, value=0)
                if st.form_submit_button("Excluir Permanentemente"):
                    conn_del = get_connection()
                    cursor = conn_del.cursor()
                    cursor.execute(f"DELETE FROM tb_extratos_regras WHERE id = {id_del} AND id_empresa = {id_empresa}")
                    conn_del.commit()
                    conn_del.close()
                    st.error("Regra excluída!"); st.rerun()
