```python
import streamlit as st
import pandas as pd
import pdfplumber
import mysql.connector
import io
import re
import unicodedata
from thefuzz import fuzz
from ofxparse import OfxParser

# Função para obter conexão com MySQL
def get_connection():
    try:
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="",
            database="concilia"
        )
        return conn
    except mysql.connector.Error as e:
        st.error(f"Erro ao conectar ao banco de dados: {e}")
        return None

# Função para padronizar texto, removendo acentos
def padronizar_texto(texto):
    if not texto:
        return ""
    texto = unicodedata.normalize('NFD', texto)
    texto = ''.join(ch for ch in texto if unicodedata.category(ch) != 'Mn')
    return texto.upper()

# Função para formatar moeda em R$ brasileiro
def formatar_moeda(valor):
    try:
        return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "R$ 0,00"

# Função para limpar CNPJ, removendo pontos, barras e traços
def limpar_cnpj(cnpj):
    if not cnpj:
        return ""
    return re.sub(r'[^0-9]', '', cnpj)

# Função para buscar empresa por CNPJ otimizado
def buscar_empresa_por_cnpj_otimizado(cnpj):
    conn = get_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM empresas WHERE cnpj = %s", (cnpj,))
        result = cursor.fetchone()
        return result
    except mysql.connector.Error as e:
        st.error(f"Erro ao buscar empresa: {e}")
        return None
    finally:
        conn.close()

# Classe UndoStack profissional para guardar e restaurar ações
class UndoStack:
    def __init__(self):
        self.stack = []

    def push(self, action):
        self.stack.append(action)

    def pop(self):
        if self.stack:
            return self.stack.pop()
        return None

    def is_empty(self):
        return len(self.stack) == 0

# Função para extrair dados por regex corrigida para PDF
def extrair_por_recintos(pdf_path):
    dados = []
    regex = r'(\d{2}/\d{2}/\d{2,4})\s+(Saída|Entrada|Saque|Depósito|Transferência|PIX|Tarifa)\s+(.*?)\s+(?:R\$?\s*)?(\d{1,3}(?:\.\d{3})*,\d{2})'
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    matches = re.findall(regex, text)
                    for match in matches:
                        data, tipo, descricao, valor = match
                        dados.append({
                            'data': data,
                            'tipo': tipo,
                            'descricao': descricao.strip(),
                            'valor': float(valor.replace('.', '').replace(',', '.'))
                        })
    except Exception as e:
        st.error(f"Erro ao extrair dados do PDF: {e}")
    return dados

# Função para extrair texto de arquivos OFX
def extrair_texto_ofx(ofx_path):
    try:
        with open(ofx_path, 'rb') as f:
            ofx = OfxParser.parse(f)
        dados = []
        for transaction in ofx.account.statement.transactions:
            dados.append({
                'data': transaction.date.strftime('%d/%m/%Y'),
                'tipo': 'Entrada' if transaction.amount > 0 else 'Saída',
                'descricao': transaction.memo,
                'valor': abs(transaction.amount)
            })
        return dados
    except Exception as e:
        st.error(f"Erro ao extrair dados do OFX: {e}")
        return []

# Função para identificar empresa no PDF
def identificar_empresa_no_pdf(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    cnpj_match = re.search(r'CNPJ:\s*([\d./-]+)', text)
                    if cnpj_match:
                        cnpj = limpar_cnpj(cnpj_match.group(1))
                        empresa = buscar_empresa_por_cnpj_otimizado(cnpj)
                        return empresa
    except Exception as e:
        st.error(f"Erro ao identificar empresa: {e}")
    return None

# Função para formatar contraparte display
def formatar_contraparte_display(empresa):
    if empresa:
        return f"{empresa['nome']} | {empresa['tipo']} | {empresa['cnpj']}"
    return "Empresa não identificada"

# Função para detectar banco automaticamente
def detectar_banco(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    if 'STONE' in text.upper():
                        return 'STONE'
                    elif 'SICOOB' in text.upper():
                        return 'SICOOB'
                    # Adicionar mais bancos conforme necessário
    except Exception as e:
        st.error(f"Erro ao detectar banco: {e}")
    return 'DESCONHECIDO'

# Inicialização do session state
if 'skipped_indices' not in st.session_state:
    st.session_state.skipped_indices = []
if 'historico_acoes' not in st.session_state:
    st.session_state.historico_acoes = []
if 'undo_stack' not in st.session_state:
    st.session_state.undo_stack = UndoStack()
if 'editando_regra_id' not in st.session_state:
    st.session_state.editando_regra_id = None

# Interface Streamlit
st.title("Aplicativo de Conciliação Bancária")

# Seção 1: Upload de PDF/OFX
st.header("1. Upload de Arquivo")
uploaded_file = st.file_uploader("Escolha um arquivo PDF ou OFX", type=['pdf', 'ofx'])
if uploaded_file:
    if uploaded_file.type == 'application/pdf':
        dados = extrair_por_recintos(io.BytesIO(uploaded_file.read()))
        banco = detectar_banco(io.BytesIO(uploaded_file.read()))
        empresa = identificar_empresa_no_pdf(io.BytesIO(uploaded_file.read()))
    elif uploaded_file.type == 'application/x-ofx':
        dados = extrair_texto_ofx(io.BytesIO(uploaded_file.read()))
        banco = 'OFX'
        empresa = None
    st.session_state.dados = dados
    st.session_state.banco = banco
    st.session_state.empresa = empresa

# Seção 2: Seleção de Empresa/Banco/Conta
st.header("2. Seleção de Empresa/Banco/Conta")
if 'empresa' in st.session_state and st.session_state.empresa:
    st.write(f"Empresa: {formatar_contraparte_display(st.session_state.empresa)}")
if 'banco' in st.session_state:
    st.write(f"Banco: {st.session_state.banco}")
# Adicionar seleção de conta contábil aqui, assumindo tabela empresa_banco_contas

# Seção 3: Auditoria de Saldos
st.header("3. Auditoria de Saldos")
if 'dados' in st.session_state:
    df = pd.DataFrame(st.session_state.dados)
    saldo_inicial = 0.0  # Assumir ou calcular
    saldo_final = df['valor'].sum() if not df.empty else 0.0
    st.metric("Saldo Inicial", formatar_moeda(saldo_inicial))
    st.metric("Saldo Final", formatar_moeda(saldo_final))

# Seção 4: Mesa de Treinamento
st.header("4. Mesa de Treinamento")
if 'dados' in st.session_state and st.session_state.dados:
    current_index = st.session_state.get('current_index', 0)
    if current_index < len(st.session_state.dados):
        item = st.session_state.dados[current_index]
        st.write(f"Item {current_index + 1}: {item}")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            if st.button("✅ Salvar Regra"):
                # Lógica para salvar regra
                st.session_state.undo_stack.push({'action': 'salvar_regra', 'index': current_index})
                st.session_state.current_index += 1
        with col2:
            if st.button("🗑️ Ignorar Lixo"):
                st.session_state.undo_stack.push({'action': 'ignorar_lixo', 'index': current_index})
                st.session_state.current_index += 1
        with col3:
            if st.button("⏭️ Pular"):
                st.session_state.undo_stack.push({'action': 'pular', 'index': current_index})
                st.session_state.current_index += 1
        with col4:
            if st.button("↩️ Desfazer"):
                action = st.session_state.undo_stack.pop()
                if action:
                    # Reverter ação
                    st.session_state.current_index = action['index']

# Seção 5: Gerenciar Regras Cadastradas
st.header("5. Gerenciar Regras Cadastradas")
# Interface para listar e editar regras

# Seção 6: Gerenciar Contas por Banco
st.header("6. Gerenciar Contas por Banco")
# Interface CRUD para empresa_banco_contas

# Seção 7: Download CSV ALTERDATA
st.header("7. Download CSV ALTERDATA")
if st.button("Baixar CSV"):
    # Gerar CSV baseado nos dados conciliados
    csv = df.to_csv(index=False)
    st.download_button("Download", csv, "concilia.csv")

# Botão Resetar Fila
if st.button("🔄 Resetar Fila"):
    st.session_state.current_index = 0
    st.session_state.skipped_indices = []
    st.session_state.undo_stack = UndoStack()
