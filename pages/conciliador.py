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
# 2. MOTOR DE LEITURA (PDF COM ARQUITETURA DE ÍMÃ E OFX)
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
    buffer_texto_topo = [] # Guarda o texto que vem ANTES do valor (O Teto)
    data_memoria = ""      # Lembra a última data vista para transações sequenciais no mesmo dia
    
    for linha in linhas_brutas:
        linha = linha.strip()
        if not linha: continue
        
        # ZONA PROIBIDA: Ignora cabeçalhos, rodapés e ruídos clássicos do banco
        linha_lower = linha.lower()
        if any(x in linha_lower for x in ["período:", "página", "saldo anterior", "saldo final", "stone institui", "data tipo descri", "cnpj"]):
            continue
            
        tem_data = re.search(r'(\d{2}/\d{2}/\d{2,4})', linha)
        tem_hora = re.search(r'(\d{2}:\d{2})', linha)
        
        # O ÍMÃ: Uma linha só é o núcleo da transação se tiver um valor financeiro (ex: 1.500,00)
        valores = re.findall(r'\b\d{1,3}(?:\.\d{3})*,\d{2}\b', linha)
        
        if tem_data:
            data_memoria = tem_data.group(1)
            
        if valores:
            # Encontrou um valor! Hora de fechar a transação anterior e criar uma nova.
            if transacao_atual:
                transacao_atual['Descricao'] = padronizar_texto(transacao_atual['Descricao'])
                dados_extraidos.append(transacao_atual)
                
            sinal = '+' if 'Entrada' in linha else '-' if 'Saída' in linha else '-' if ' - R$' in linha else '+'
            valor_num = float(valores[0].replace('.', '').replace(',', '.'))
            
            # Limpa as âncoras para sobrar só o texto útil
            desc_linha = linha
            if tem_data: desc_linha = desc_linha.replace(tem_data.group(0), '')
            if tem_hora: desc_linha = desc_linha.replace(tem_hora.group(0), '')
            for v in valores: desc_linha = desc_linha.replace(v, '')
            desc_linha = re.sub(r'\b(Entrada|Saída|R\$|-)\b', '', desc_linha, flags=re.IGNORECASE).strip()
            
            # A nova transação nasce unindo o Teto (buffer) com a linha atual
            desc_completa = " ".join(buffer_texto_topo) + " " + desc_linha
            buffer_texto_topo = [] # Esvazia o teto, pois já foi usado
            
            transacao_atual = {
                'Data': data_memoria,
                'Descricao': desc_completa,
                'Valor': abs(valor_num),
                'Sinal': sinal
            }
        else:
            # SE NÃO TEM VALOR, é uma descrição flutuante.
            # Se já temos uma transação aberta, gruda no "Chão" dela (abaixo).
            # Se não, guarda no "Teto" da próxima que vier.
            texto_limpo = linha.replace('R$', '').strip()
            if transacao_atual:
                transacao_atual['Descricao'] += " " + texto_limpo
            else:
                buffer_texto_topo.append(texto_limpo)
                
    # Salva a última transação lida ao final do arquivo
    if transacao_atual:
        transacao_atual['Descricao'] = padronizar_texto(transacao_atual['Descricao'])
        dados_extraidos.append(transacao_atual)
        
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
    conn_setup = get_connection()
    empresas = pd.read_sql("SELECT id, nome FROM empresas", conn_setup)
    conn_setup.close()
    
    empresa_sel = col_cfg1.selectbox("Empresa / Filial", empresas['nome'])
    id_empresa = int(empresas[empresas['nome'] == empresa_sel]['id'].values[0])
    conta_banco_fixa = col_cfg2.text_input("Conta Contábil do Banco (Âncora)", value="196")
except Exception as e:
    st.error(f"Erro ao carregar dados iniciais. Verifique a conexão.")
    st.stop()

uploaded_files = st.file_uploader("Suba os arquivos (PDF ou OFX)", type=["pdf", "ofx"], accept_multiple_files=True)

if uploaded_files and conta_banco_fixa:
    with st.spinner("Lendo arquivos, separando zonas e calculando similaridade neural..."):
        
        lista_dfs = []
        for file in uploaded_files:
            file_name = file.name.lower()
            if file_name.endswith('.pdf'):
                lista_dfs.append(extrair_texto_pdf(file.getvalue()))
            elif file_name.endswith('.ofx'):
                lista_dfs.append(extrair_texto_ofx(file.getvalue()))
        
        df_bruto = pd.concat(lista_dfs, ignore_index=True)
        
        conn_regras = get_connection()
        regras = pd.read_sql(f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa}", conn_regras)
        conn_regras.close()

    prontos_alterdata = []
    pendentes_treinamento = []

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
            termo = st.text_input("Termo Chave (Corte a sujeira e deixe a raiz)", value=top['Descricao'])
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
