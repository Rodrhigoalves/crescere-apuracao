import streamlit as st
import pandas as pd
import pdfplumber
import mysql.connector
import io
import re

# ---------------------------------------------------------
# 1. CONEXÃO COM O BANCO UOL
# ---------------------------------------------------------
def get_connection():
    return mysql.connector.connect(
        host=st.secrets["mysql"]["host"],
        user=st.secrets["mysql"]["user"],
        password=st.secrets["mysql"]["password"],
        database=st.secrets["mysql"]["database"]
    )

# ---------------------------------------------------------
# 2. LEITOR COM SUPORTE A HORAS (SEPARADOR INTELIGENTE)
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
        
        # GATILHO: Nova transação começa com DATA (00/00) ou HORA (00:00)
        tem_data = re.search(r'(\d{2}/\d{2}/\d{2,4})', linha)
        tem_hora = re.search(r'(\d{2}:\d{2})', linha)
        
        if tem_data or tem_hora:
            if transacao_atual:
                dados_extraidos.append(transacao_atual)
            
            # Puxa a data da linha, ou herda da transação anterior se só tiver a hora
            data = tem_data.group(1) if tem_data else (transacao_atual['Data'] if transacao_atual else "")
            sinal = '+' if 'Entrada' in linha else '-' if 'Saída' in linha else '-' if ' - R$' in linha else '+'
            
            valores = re.findall(r'\d{1,3}(?:\.\d{3})*,\d{2}', linha)
            valor_num = float(valores[0].replace('.', '').replace(',', '.')) if valores else 0.0
            
            # Limpeza cirúrgica da descrição
            desc = linha
            if tem_data: desc = desc.replace(tem_data.group(0), '')
            if tem_hora: desc = desc.replace(tem_hora.group(0), '')
            for v in valores: desc = desc.replace(v, '')
            desc = desc.replace('Entrada', '').replace('Saída', '').replace('R$', '').replace('-', '').strip()
            desc = re.sub(r'\s+', ' ', desc)
            
            transacao_atual = {'Data': data, 'Descricao': desc, 'Valor': abs(valor_num), 'Sinal': sinal}
        else:
            # Continuação da linha anterior (ex: Pix | Maquininha)
            if transacao_atual and len(linha) > 2 and "Saldo" not in linha and "Página" not in linha:
                transacao_atual['Descricao'] += f" {linha.replace('R$', '').strip()}"
                
    if transacao_atual: 
        dados_extraidos.append(transacao_atual)
        
    return pd.DataFrame(dados_extraidos)

# ---------------------------------------------------------
# 3. INTERFACE PRINCIPAL
# ---------------------------------------------------------
st.set_page_config(page_title="Conciliador", page_icon="🎯", layout="wide")
st.title("🎯 Conciliador de Extratos Pro")

# Configurações do Cabeçalho
col_cfg1, col_cfg2 = st.columns([2, 1])
try:
    conn = get_connection()
    empresas = pd.read_sql("SELECT id, nome FROM empresas", conn)
    empresa_sel = col_cfg1.selectbox("Empresa / Filial", empresas['nome'])
    id_empresa = int(empresas[empresas['nome'] == empresa_sel]['id'].values[0])
    conta_banco_fixa = col_cfg2.text_input("Conta Contábil do Banco (Âncora)", value="196")
except Exception as e:
    st.error("Erro de conexão com o banco UOL.")
    st.stop()

uploaded_file = st.file_uploader("Suba o PDF (Stone, etc.)", type="pdf")

if uploaded_file and conta_banco_fixa:
    with st.spinner("Processando páginas e cruzando inteligência..."):
        df_bruto = extrair_texto_pdf(uploaded_file.getvalue())
        regras = pd.read_sql(f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa}", conn)

    prontos_alterdata = []
    pendentes_treinamento = []

    # Motor de Classificação
    for _, row in df_bruto.iterrows():
        match_encontrado = False
        ignorar_linha = False
        
        for _, r in regras.iterrows():
            if r['termo_chave'].upper() in row['Descricao'].upper() and r['sinal_esperado'] == row['Sinal']:
                
                # Regra de Lixo (Descarta a linha)
                if r['conta_contabil'] == 'IGNORAR':
                    ignorar_linha = True
                    break
                
                # Lógica de D/C Automática
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
    # 4. PAINEL DE RESULTADOS E TRAVA
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
    # 5. MESA DE TREINAMENTO (COM BOTÃO DE LIXO)
    # ---------------------------------------------------------
    if not df_pendentes.empty:
        st.divider()
        pendentes_agrupados = df_pendentes.groupby(['Descricao', 'Sinal']).size().reset_index(name='Qtd').sort_values('Qtd', ascending=False)
        top = pendentes_agrupados.iloc[0]
        
        st.subheader(f"🎓 Treinando: {top['Qtd']} operações encontradas")
        
        with st.form("form_treinamento", clear_on_submit=True):
            termo = st.text_input("Termo Chave (Deixe só a palavra em comum. Ex: MATHEUS)", value=top['Descricao'])
            st.caption(f"Operação lida como **{'🟢 ENTRADA' if top['Sinal'] == '+' else '🔴 SAÍDA'}**. Conta Fixa: {conta_banco_fixa}")
            
            c1, c2, c3 = st.columns([1, 1, 2])
            conta = c1.text_input("Conta Contrapartida")
            cod_hist = c2.text_input("Cód. Hist.")
            txt_hist = c3.text_input("Texto do Histórico (Opcional)")
            
            b1, b2 = st.columns(2)
            submit_salvar = b1.form_submit_button("✅ Salvar Regra de Conta")
            submit_lixo = b2.form_submit_button("🗑️ Ignorar / Descartar Lixo")
            
            if (submit_salvar and conta) or submit_lixo:
                conta_final = 'IGNORAR' if submit_lixo else conta
                cursor = conn.cursor()
                cursor.execute("""INSERT INTO tb_extratos_regras 
                               (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil, cod_historico_erp, historico_padrao) 
                               VALUES (%s, %s, %s, %s, %s, %s, %s)""", 
                               (id_empresa, 'STONE', termo.upper(), top['Sinal'], conta_final, cod_hist if cod_hist else None, txt_hist))
                conn.commit()
                st.rerun()

    # ---------------------------------------------------------
    # 6. GERENCIADOR DE REGRAS (EDICAO E EXCLUSAO)
    # ---------------------------------------------------------
    st.divider()
    with st.expander("⚙️ Gerenciar Banco de Inteligência (Ver, Editar ou Excluir)"):
        regras_view = pd.read_sql(f"SELECT id, termo_chave, sinal_esperado, conta_contabil as contrapartida, historico_padrao FROM tb_extratos_regras WHERE id_empresa = {id_empresa}", conn)
        st.dataframe(regras_view, use_container_width=True)
        
        c_ed1, c_ed2 = st.columns(2)
        
        with c_ed1:
            st.write("**Editar Conta**")
            with st.form("form_edit"):
                id_edit = st.number_input("ID da Regra", step=1, value=0)
                nova_conta = st.text_input("Nova Conta Contrapartida")
                if st.form_submit_button("Atualizar Conta"):
                    cursor = conn.cursor()
                    cursor.execute(f"UPDATE tb_extratos_regras SET conta_contabil = '{nova_conta}' WHERE id = {id_edit} AND id_empresa = {id_empresa}")
                    conn.commit()
                    st.success("Conta atualizada!"); st.rerun()
                    
        with c_ed2:
            st.write("**Excluir Regra**")
            with st.form("form_del"):
                id_del = st.number_input("ID para Excluir", step=1, value=0)
                if st.form_submit_button("Excluir Permanentemente"):
                    cursor = conn.cursor()
                    cursor.execute(f"DELETE FROM tb_extratos_regras WHERE id = {id_del} AND id_empresa = {id_empresa}")
                    conn.commit()
                    st.error("Regra excluída!"); st.rerun()

conn.close()
