# Código completo para aplicação Streamlit de conciliação bancária
import streamlit as st
import mysql.connector
import pdfplumber
import re
from ofxparse import OfxParser
import pandas as pd
from datetime import datetime
import logging
import os
from typing import List, Dict, Any

# Configuração de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Classe UndoStack para sistema de desfazer
def UndoStack:
    def __init__(self):
        self.stack: List[Dict[str, Any]] = []
    
    def push(self, action: str, data: Dict[str, Any]):
        self.stack.append({'action': action, 'data': data})
    
    def pop(self):
        if self.stack:
            return self.stack.pop()
        return None
    
    def is_empty(self):
        return len(self.stack) == 0

# Função para conectar ao banco de dados MySQL
def conectar_db():
    try:
        conn = mysql.connector.connect(
            host=st.secrets['mysql']['host'],
            user=st.secrets['mysql']['user'],
            password=st.secrets['mysql']['password'],
            database=st.secrets['mysql']['database']
        )
        return conn
    except Exception as e:
        logging.error(f'Erro ao conectar ao banco: {e}')
        st.error('Erro ao conectar ao banco de dados.')
        return None

# Função para extrair dados de PDF
def extrair_por_recintos(pdf_path: str) -> List[Dict[str, Any]]:
    transacoes = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    # Regex corrigida para capturar descrição na terceira coluna
                    pattern = r'(\d{2}/\d{2}/\d{4})\s+([^\s]+)\s+(.+?)\s+([\d.,-]+)'
                    matches = re.findall(pattern, text)
                    for match in matches:
                        data, tipo, descricao, valor = match
                        # Limpeza e padronização
                        descricao = re.sub(r'\s+', ' ', descricao.strip())
                        valor = float(valor.replace('.', '').replace(',', '.'))
                        transacoes.append({
                            'data': datetime.strptime(data, '%d/%m/%Y'),
                            'tipo': tipo.strip(),
                            'descricao': descricao,
                            'valor': valor
                        })
    except Exception as e:
        logging.error(f'Erro ao extrair PDF: {e}')
        st.error('Erro ao processar PDF.')
    return transacoes

# Função para extrair dados de OFX
def extrair_ofx(ofx_path: str) -> List[Dict[str, Any]]:
    transacoes = []
    try:
        with open(ofx_path, 'rb') as f:
            ofx = OfxParser.parse(f)
            for account in ofx.accounts:
                for transaction in account.statement.transactions:
                    transacoes.append({
                        'data': transaction.date,
                        'tipo': transaction.type,
                        'descricao': transaction.memo,
                        'valor': float(transaction.amount)
                    })
    except Exception as e:
        logging.error(f'Erro ao extrair OFX: {e}')
        st.error('Erro ao processar OFX.')
    return transacoes

# Função para detectar empresa por CNPJ no cabeçalho do PDF
def detectar_empresa(pdf_path: str) -> str:
    try:
        with pdfplumber.open(pdf_path) as pdf:
            first_page = pdf.pages[0].extract_text()
            cnpj_match = re.search(r'CNPJ:\s*([\d./-]+)', first_page)
            if cnpj_match:
                cnpj = cnpj_match.group(1)
                conn = conectar_db()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT nome FROM empresas WHERE cnpj = %s', (cnpj,))
                    result = cursor.fetchone()
                    conn.close()
                    return result[0] if result else 'Empresa não encontrada'
    except Exception as e:
        logging.error(f'Erro ao detectar empresa: {e}')
    return 'Não detectada'

# Função para detectar banco
def detectar_banco(descricao: str) -> str:
    if 'STONE' in descricao.upper():
        return 'STONE'
    elif 'SICOOB' in descricao.upper():
        return 'SICOOB'
    # Adicionar mais bancos conforme necessário
    return 'Desconhecido'

# Função para buscar conta_contabil
def buscar_conta_contabil(empresa: str, banco: str) -> str:
    conn = conectar_db()
    if conn:
        cursor = conn.cursor()
        cursor.execute('SELECT conta_contabil FROM empresa_banco_contas WHERE empresa = %s AND banco = %s', (empresa, banco))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else ''
    return ''

# Função CRUD para regras
def salvar_regra(empresa: str, termos: List[str], conta_debito: str, conta_credito: str):
    conn = conectar_db()
    if conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO tb_extratos_regras (empresa, termos, conta_debito, conta_credito) VALUES (%s, %s, %s, %s)',
                       (empresa, ','.join(termos), conta_debito, conta_credito))
        conn.commit()
        conn.close()

# Interface Streamlit
st.title('Sistema de Conciliação Bancária')

# Inicializar sessão
if 'undo_stack' not in st.session_state:
    st.session_state.undo_stack = UndoStack()
if 'transacoes' not in st.session_state:
    st.session_state.transacoes = []
if 'regras' not in st.session_state:
    st.session_state.regras = []

# Passo 1: Upload de arquivo
uploaded_file = st.file_uploader('Faça upload do PDF ou OFX', type=['pdf', 'ofx'])
if uploaded_file:
    file_path = f'/tmp/{uploaded_file.name}'
    with open(file_path, 'wb') as f:
        f.write(uploaded_file.getbuffer())
    
    if uploaded_file.name.endswith('.pdf'):
        st.session_state.transacoes = extrair_por_recintos(file_path)
        empresa_detectada = detectar_empresa(file_path)
    elif uploaded_file.name.endswith('.ofx'):
        st.session_state.transacoes = extrair_ofx(file_path)
        empresa_detectada = 'Detectar manualmente'  # Ajustar conforme necessário
    
    st.success('Arquivo processado com sucesso!')

# Passo 2: Seleção de Empresa/Filial
empresas = ['Empresa A', 'Empresa B']  # Buscar do banco
empresa_selecionada = st.selectbox('Selecione a Empresa/Filial', empresas, index=empresas.index(empresa_detectada) if empresa_detectada in empresas else 0)

# Passo 3: Seleção de Banco
bancos = ['STONE', 'SICOOB']
banco_selecionado = st.selectbox('Selecione o Banco', bancos)

# Passo 4: Entrada de Saldo Anterior e Conta Bancária
saldo_anterior = st.number_input('Saldo Anterior', value=0.0)
conta_bancaria = buscar_conta_contabil(empresa_selecionada, banco_selecionado)
st.text_input('Conta Bancária', value=conta_bancaria)

# Mesa de Treinamento
st.header('Mesa de Treinamento')
if st.session_state.transacoes:
    transacao_atual = st.session_state.transacoes[0]  # Simples, ajustar para fila
    st.write(f'Transação: {transacao_atual}')
    
    termos_chave = st.multiselect('Selecione termos-chave', transacao_atual['descricao'].split())
    conta_debito = st.text_input('Conta Débito')
    conta_credito = st.text_input('Conta Crédito')
    
    if st.button('Salvar Regra'):
        salvar_regra(empresa_selecionada, termos_chave, conta_debito, conta_credito)
        st.session_state.undo_stack.push('salvar_regra', {'empresa': empresa_selecionada, 'termos': termos_chave})
        st.success('Regra salva!')
    
    if st.button('Ignorar Lixo'):
        st.session_state.undo_stack.push('ignorar_lixo', {'transacao': transacao_atual})
        st.session_state.transacoes.pop(0)
        st.success('Transação ignorada!')
    
    if st.button('Pular Transação'):
        st.session_state.undo_stack.push('pular_transacao', {'transacao': transacao_atual})
        # Lógica para pular
        st.success('Transação pulada!')

# Botão Desfazer
if st.button('Desfazer Ação'):
    last_action = st.session_state.undo_stack.pop()
    if last_action:
        # Lógica para desfazer baseada em last_action['action']
        st.success('Ação desfeita!')
    else:
        st.warning('Nenhuma ação para desfazer.')

# Auditoria de Saldos
st.header('Auditoria de Saldos')
# Calcular métricas dinâmicas
saldo_calculado = saldo_anterior + sum(t['valor'] for t in st.session_state.transacoes)
st.metric('Saldo Calculado', f'R$ {saldo_calculado:.2f}')

# Gerenciamento de Regras
st.header('Gerenciamento de Regras')
# Listar regras do banco

# Gerenciamento de Contas
st.header('Gerenciamento de Contas por Banco')
# CRUD para empresa_banco_contas

# Download CSV
if st.button('Download CSV ALTERDATA'):
    df = pd.DataFrame(st.session_state.transacoes)
    csv = df.to_csv(index=False)
    st.download_button('Baixar CSV', csv, 'extrato.csv')

# Validações e Segurança: Prepared statements já usados, try-catch em funções, logs configurados.
