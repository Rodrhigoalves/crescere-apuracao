import streamlit as st
import pandas as pd
import pdfplumber
import mysql.connector
import io

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
# 2. LEITURA DO PDF (COM CACHE PARA EXTRATOS GIGANTES)
# ---------------------------------------------------------
# O st.cache_data garante que as 170 páginas sejam lidas só uma vez.
@st.cache_data(show_spinner=False)
def extrair_texto_pdf(file_bytes):
    linhas_extraidas = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            texto = page.extract_text()
            if not texto: continue
            
            for linha in texto.split('\n'):
                # Filtra apenas linhas que parecem ter uma data no início
                if len(linha) > 10 and linha[2] == '/' and linha[5] == '/':
                    partes = linha.split()
                    try:
                        data = partes[0]
                        valor_str = partes[-1]
                        desc_original = " ".join(partes[1:-1])
                        
                        valor_limpo = valor_str.replace('R$', '').replace('.', '').replace(',', '.').strip()
                        valor_num = float(valor_limpo)
                        
                        sinal = '-' if '-' in valor_str or valor_num < 0 else '+'
                        
                        linhas_extraidas.append({
                            'Data': data,
                            'Descricao': desc_original,
                            'Valor': abs(valor_num),
                            'Sinal': sinal
                        })
                    except:
                        continue
    return pd.DataFrame(linhas_extraidas)

# ---------------------------------------------------------
# 3. INTERFACE PRINCIPAL
# ---------------------------------------------------------
st.set_page_config(page_title="Conciliador", page_icon="🎯", layout="wide")
st.title("🎯 Conciliador de Extratos Inteligente")

# Configurações Iniciais da Empresa e Banco
col_cfg1, col_cfg2 = st.columns([2, 1])
with col_cfg1:
    conn = get_connection()
    empresas = pd.read_sql("SELECT id, nome FROM empresas", conn)
    conn.close()
    empresa_sel = st.selectbox("Empresa / Filial", empresas['nome'])
    id_empresa = int(empresas[empresas['nome'] == empresa_sel]['id'].values[0])

with col_cfg2:
    conta_banco_fixa = st.text_input("Conta Contábil do Banco (Ex: 196)", value="196")

uploaded_file = st.file_uploader("Suba o PDF (Stone, etc.)", type="pdf")

if uploaded_file and conta_banco_fixa:
    # 1. Lê o PDF (rápido devido ao cache)
    with st.spinner("Lendo arquivo (Isso pode levar um tempo para extratos longos)..."):
        file_bytes = uploaded_file.getvalue()
        df_bruto = extrair_texto_pdf(file_bytes)
    
    # 2. Busca as regras já treinadas no UOL
    conn = get_connection()
    regras = pd.read_sql(f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa}", conn)
    conn.close()

    # 3. Aplica o cruzamento de dados
    prontos_alterdata = []
    pendentes_treinamento = []

    for _, row in df_bruto.iterrows():
        match_encontrado = False
        
        for _, r in regras.iterrows():
            if r['termo_chave'].upper() in row['Descricao'].upper() and r['sinal_esperado'] == row['Sinal']:
                # Lógica Automática de D/C baseada na conta fixa
                conta_contrapartida = r['conta_contabil'] # A conta que você mapeou
                
                if row['Sinal'] == '+': # Entrada de Dinheiro
                    debito = conta_banco_fixa
                    credito = conta_contrapartida
                else: # Saída de Dinheiro
                    debito = conta_contrapartida
                    credito = conta_banco_fixa
                
                # Regras de Histórico exigidas pelo ERP
                cod_hist = str(int(r['cod_historico_erp'])) if pd.notna(r['cod_historico_erp']) else ""
                txt_hist = r['historico_padrao'] if pd.notna(r['historico_padrao']) else row['Descricao']

                prontos_alterdata.append({
                    'Debito': debito, 'Credito': credito, 'Data': row['Data'],
                    'Valor': f"{row['Valor']:.2f}".replace('.', ','),
                    'Cod. Historico': cod_hist, 'Historico': txt_hist,
                    'Nr. Documento': '', 'Cod. Centro Custo': '', 'Livro': '', 'Filial': '', 'Tipo': ''
                })
                match_encontrado = True
                break
        
        if not match_encontrado:
            pendentes_treinamento.append(row)

    # ---------------------------------------------------------
    # 4. PAINEL DE RESULTADOS E DOWNLOAD
    # ---------------------------------------------------------
    df_prontos = pd.DataFrame(prontos_alterdata)
    df_pendentes = pd.DataFrame(pendentes_treinamento)

    colA, colB = st.columns(2)
    colA.success(f"✅ Prontos para o Alterdata: {len(df_prontos)} linhas")
    colB.error(f"⚠️ Pendentes de Treinamento: {len(df_pendentes)} linhas")

    if not df_prontos.empty:
        csv_data = df_prontos.to_csv(index=False, sep=';', encoding='latin1')
        st.download_button(
            label="📥 BAIXAR ARQUIVO DE EXPORTAÇÃO (.CSV)",
            data=csv_data,
            file_name=f"exportacao_stone_{empresa_sel}.csv",
            mime="text/csv",
            type="primary"
        )

    # ---------------------------------------------------------
    # 5. MESA DE TREINAMENTO INTELIGENTE
    # ---------------------------------------------------------
    if not df_pendentes.empty:
        st.divider()
        st.subheader("🎓 Treinar Sistema (Resolver Pendências)")
        
        # Agrupa itens iguais para você resolver vários de uma vez
        pendentes_agrupados = df_pendentes.groupby(['Descricao', 'Sinal']).size().reset_index(name='Quantidade')
        pendentes_agrupados = pendentes_agrupados.sort_values(by='Quantidade', ascending=False)
        
        # Pega o problema que mais se repete no PDF para resolver primeiro
        top_pendencia = pendentes_agrupados.iloc[0]
        
        st.write(f"**A operação abaixo aparece {top_pendencia['Quantidade']} vezes neste extrato:**")
        
        with st.form("form_treinamento", clear_on_submit=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                termo_sugerido = st.text_input("Termo de Busca (Apague nomes de pessoas/CPFs e deixe só a raiz)", value=top_pendencia['Descricao'])
            with col2:
                # Mostra visualmente se o sistema leu como entrada ou saída
                tipo_op = "🟢 ENTRADA (+)" if top_pendencia['Sinal'] == '+' else "🔴 SAÍDA (-)"
                st.text_input("Comportamento no Banco", value=tipo_op, disabled=True)
            
            st.caption(f"📌 Lógica automática: Sendo {tipo_op}, a conta {conta_banco_fixa} já será o {'DÉBITO' if top_pendencia['Sinal'] == '+' else 'CRÉDITO'}.")
            
            col3, col4, col5 = st.columns([1, 1, 2])
            with col3:
                conta_contra = st.text_input("Conta Contrapartida")
            with col4:
                cod_historico = st.text_input("Cód. Hist. (Opcional)")
            with col5:
                hist_texto = st.text_input("Texto Padrão do Histórico")
            
            submit = st.form_submit_button("Salvar Regra e Reprocessar")
            
            if submit and conta_contra:
                # Salva no banco UOL
                conn = get_connection()
                cursor = conn.cursor()
                sql = """INSERT INTO tb_extratos_regras 
                         (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil, cod_historico_erp, historico_padrao) 
                         VALUES (%s, %s, %s, %s, %s, %s, %s)"""
                # Salvamos a contrapartida na coluna conta_contabil para reaproveitar a tabela atual
                val = (id_empresa, 'STONE', termo_sugerido.upper(), top_pendencia['Sinal'], conta_contra, cod_historico if cod_historico else None, hist_texto)
                cursor.execute(sql, val)
                conn.commit()
                conn.close()
                
                st.success("Regra salva! Atualizando extrato...")
                st.rerun() # Força a tela a recarregar instantaneamente e aplicar a regra

        # Mostra as outras pendências menores abaixo
        with st.expander("Ver lista completa de não mapeados"):
            st.dataframe(pendentes_agrupados)

    elif df_bruto.empty == False:
        st.balloons()
        st.success("🎉 Parabéns! 100% do extrato foi mapeado. O arquivo já pode ser importado.")
