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
# 2. LEITURA INTELIGENTE POR BLOCOS (COM CACHE)
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
        
        # Tenta identificar se a linha começa com uma Data (ex: 31/01/25 ou 31/01/2025)
        match_data = re.match(r'^(\d{2}/\d{2}/\d{2,4})', linha)
        
        if match_data:
            # Se achou uma data nova, guarda a transação anterior na lista
            if transacao_atual:
                dados_extraidos.append(transacao_atual)
            
            data = match_data.group(1)
            
            # Identifica se é Entrada ou Saída
            sinal = '+' if 'Entrada' in linha else '-' if 'Saída' in linha else '-' if ' - R$' in linha else '+'
            
            # Captura todos os valores monetários da linha (ex: 10,50 e 37.917,30)
            valores = re.findall(r'\d{1,3}(?:\.\d{3})*,\d{2}', linha)
            valor_num = 0.0
            if valores:
                # O primeiro valor encontrado é o da transação (o segundo geralmente é o saldo)
                valor_num = float(valores[0].replace('.', '').replace(',', '.'))
            
            # Limpa a linha para deixar SÓ a descrição principal
            desc = linha.replace(data, '').replace('Entrada', '').replace('Saída', '')
            for v in valores:
                desc = desc.replace(v, '') # Remove os valores da descrição
            desc = desc.replace('R$', '').replace('-', '').strip()
            desc = re.sub(r'\s+', ' ', desc) # Remove espaços duplos
            
            transacao_atual = {
                'Data': data,
                'Descricao': desc,
                'Valor': abs(valor_num),
                'Sinal': sinal
            }
        else:
            # SE NÃO TEM DATA: É o complemento da linha anterior! (ex: "Pix | Maquininha")
            if transacao_atual and len(linha) > 2 and "Saldo" not in linha and "Página" not in linha:
                # Limpa sujeiras antes de juntar
                complemento = linha.replace('R$', '').strip()
                transacao_atual['Descricao'] += f" {complemento}"
                
    # Guarda a última transação lida
    if transacao_atual:
        dados_extraidos.append(transacao_atual)
        
    return pd.DataFrame(dados_extraidos)

# ---------------------------------------------------------
# 3. INTERFACE PRINCIPAL
# ---------------------------------------------------------
st.set_page_config(page_title="Conciliador", page_icon="🎯", layout="wide")
st.title("🎯 Conciliador de Extratos Inteligente")

# Configurações Iniciais
col_cfg1, col_cfg2 = st.columns([2, 1])
try:
    with col_cfg1:
        conn = get_connection()
        empresas = pd.read_sql("SELECT id, nome FROM empresas", conn)
        conn.close()
        empresa_sel = st.selectbox("Empresa / Filial", empresas['nome'])
        id_empresa = int(empresas[empresas['nome'] == empresa_sel]['id'].values[0])

    with col_cfg2:
        conta_banco_fixa = st.text_input("Conta Contábil do Banco (Ex: 196)", value="196")
except Exception as e:
    st.error("Erro ao carregar banco de dados. Verifique a conexão.")
    st.stop()

uploaded_file = st.file_uploader("Suba o PDF (Stone, etc.)", type="pdf")

if uploaded_file and conta_banco_fixa:
    with st.spinner("Lendo páginas e agrupando blocos de texto..."):
        file_bytes = uploaded_file.getvalue()
        df_bruto = extrair_texto_pdf(file_bytes)
    
    conn = get_connection()
    regras = pd.read_sql(f"SELECT * FROM tb_extratos_regras WHERE id_empresa = {id_empresa}", conn)
    conn.close()

    prontos_alterdata = []
    pendentes_treinamento = []

    for _, row in df_bruto.iterrows():
        match_encontrado = False
        
        for _, r in regras.iterrows():
            if r['termo_chave'].upper() in row['Descricao'].upper() and r['sinal_esperado'] == row['Sinal']:
                conta_contrapartida = r['conta_contabil']
                
                # Lógica D/C Automática
                if row['Sinal'] == '+': # Entrada
                    debito = conta_banco_fixa
                    credito = conta_contrapartida
                else: # Saída
                    debito = conta_contrapartida
                    credito = conta_banco_fixa
                
                cod_hist = str(int(r['cod_historico_erp'])) if pd.notna(r['cod_historico_erp']) else ""
                txt_hist = r['historico_padrao'] if pd.notna(r['historico_padrao']) and str(r['historico_padrao']).strip() != "" else row['Descricao']

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
    # 4. PAINEL DE RESULTADOS E TRAVA DE EXPORTAÇÃO
    # ---------------------------------------------------------
    df_prontos = pd.DataFrame(prontos_alterdata)
    df_pendentes = pd.DataFrame(pendentes_treinamento)

    colA, colB = st.columns(2)
    colA.success(f"✅ Prontos para o ERP: {len(df_prontos)} linhas")
    colB.error(f"⚠️ Pendentes (Não Mapeados): {len(df_pendentes)} linhas")

    st.divider()

    # Lógica da Trava (Só libera o download se não houver pendências)
    if df_pendentes.empty and not df_prontos.empty:
        st.balloons()
        st.success("🎉 100% do extrato mapeado! O ficheiro de exportação foi liberado.")
        csv_data = df_prontos.to_csv(index=False, sep=';', encoding='latin1')
        st.download_button(
            label="📥 BAIXAR ARQUIVO PARA ALTERDATA (.CSV)",
            data=csv_data,
            file_name=f"exportacao_stone_{empresa_sel}.csv",
            mime="text/csv",
            type="primary",
            use_container_width=True
        )
    elif not df_pendentes.empty:
        st.warning(f"🔒 A exportação está bloqueada. Faltam mapear {len(df_pendentes)} operações para garantir a integridade do saldo.")

    # ---------------------------------------------------------
    # 5. MESA DE TREINAMENTO INTELIGENTE
    # ---------------------------------------------------------
    if not df_pendentes.empty:
        st.subheader("🎓 Mesa de Treinamento")
        
        pendentes_agrupados = df_pendentes.groupby(['Descricao', 'Sinal']).size().reset_index(name='Quantidade')
        pendentes_agrupados = pendentes_agrupados.sort_values(by='Quantidade', ascending=False)
        
        top_pendencia = pendentes_agrupados.iloc[0]
        
        st.write(f"**A operação abaixo aparece {top_pendencia['Quantidade']} vezes neste extrato:**")
        
        with st.form("form_treinamento", clear_on_submit=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                # O texto já vem sem R$ e sem valores, facilitando o corte
                termo_sugerido = st.text_input("Termo de Busca (Corte nomes e deixe só a operação)", value=top_pendencia['Descricao'])
            with col2:
                tipo_op = "🟢 ENTRADA (+)" if top_pendencia['Sinal'] == '+' else "🔴 SAÍDA (-)"
                st.text_input("Sinal lido no PDF", value=tipo_op, disabled=True)
            
            st.caption(f"📌 Sendo {tipo_op}, o banco {conta_banco_fixa} será o **{'DÉBITO' if top_pendencia['Sinal'] == '+' else 'CRÉDITO'}**. Qual a conta da Contrapartida?")
            
            col3, col4, col5 = st.columns([1, 1, 2])
            with col3:
                conta_contra = st.text_input("Conta Contrapartida")
            with col4:
                cod_historico = st.text_input("Cód. Hist. (Opcional)")
            with col5:
                hist_texto = st.text_input("Texto do Histórico (Opcional)")
            
            submit = st.form_submit_button("Salvar Regra e Recalcular")
            
            if submit and conta_contra:
                conn = get_connection()
                cursor = conn.cursor()
                sql = """INSERT INTO tb_extratos_regras 
                         (id_empresa, banco_nome, termo_chave, sinal_esperado, conta_contabil, cod_historico_erp, historico_padrao) 
                         VALUES (%s, %s, %s, %s, %s, %s, %s)"""
                val = (id_empresa, 'STONE', termo_sugerido.upper(), top_pendencia['Sinal'], conta_contra, cod_historico if cod_historico else None, hist_texto)
                cursor.execute(sql, val)
                conn.commit()
                conn.close()
                
                st.success("Regra salva! Atualizando a tela...")
                st.rerun()

        with st.expander("Ver fila completa de Não Mapeados"):
            st.dataframe(pendentes_agrupados)
