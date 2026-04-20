import streamlit as st
import pandas as pd
from docxtpl import DocxTemplate
import io
import zipfile
import os
import re

# Configuração da página para o monitor do ASUS TUF
st.set_page_config(page_title="Gerador de Informes Pro", layout="wide")

st.title("📄 Gerador de Informe de Rendimentos")
st.markdown("---")

# --- FUNÇÕES DE FORMATAÇÃO E TRATAMENTO ---

def formatar_moeda(valor):
    """Garante o formato 6.500,00 ou 0,00 sem o R$"""
    try:
        if pd.isna(valor) or valor == "" or valor == 0:
            return "0,00"
        return f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "0,00"

def formatar_documento(doc):
    """Aplica máscara de CPF/CNPJ garantindo zeros à esquerda"""
    doc = re.sub(r'\D', '', str(doc)).strip()
    if not doc:
        return ""

    if len(doc) <= 11:
        doc = doc.zfill(11)
        return f"{doc[:3]}.{doc[3:6]}.{doc[6:9]}-{doc[9:]}"
    elif len(doc) <= 14:
        doc = doc.zfill(14)
        return f"{doc[:2]}.{doc[2:5]}.{doc[5:8]}/{doc[8:12]}-{doc[12:]}"
    return doc

def formatar_data_br(data_valor):
    """
    Trata a data individualmente para cada linha:
    Converte para o padrão dd/mm/aaaa, aceitando objetos de data ou strings.
    """
    try:
        if pd.isna(data_valor) or str(data_valor).strip() == "":
            return None # Retorna None para indicar que deve usar o padrão do código
        
        # Converte para datetime (trata tanto o formato do Excel quanto strings isoladas)
        dt = pd.to_datetime(data_valor)
        return dt.strftime('%d/%m/%Y')
    except:
        # Se for uma string que já está no formato manual, limpa possíveis horas
        s_data = str(data_valor).strip()
        return s_data.split(' ')[0] if ' ' in s_data else s_data

def localizar_template():
    """Busca automática de qualquer arquivo .docx na raiz do repositório no GitHub"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    raiz = os.path.normpath(os.path.join(current_dir, ".."))
    try:
        arquivos = os.listdir(raiz)
        for arq in arquivos:
            if arq.lower().endswith(".docx"):
                return os.path.join(raiz, arq)
    except:
        return None
    return None

# --- VERIFICAÇÃO DO TEMPLATE ---

template_path = localizar_template()

if template_path:
    st.success(f"✅ Template detectado: **{os.path.basename(template_path)}**")
else:
    st.error("❌ Erro: Nenhum arquivo .docx encontrado na raiz do projeto no GitHub.")
    st.stop()

# --- INTERFACE E PROCESSAMENTO ---

uploaded_file = st.file_uploader("Carregue a planilha Excel", type=["xlsx"])

if uploaded_file:
    try:
        # Lendo o Excel preservando zeros à esquerda e garantindo que campos de data não sejam corrompidos
        df = pd.read_excel(uploaded_file, dtype={'cnpj_fonte': str, 'cpf_beneficiario': str})
        
        st.write(f"Registros encontrados: {len(df)}")
        st.dataframe(df.head())

        if st.button("🚀 Gerar Informes (ZIP com Datas Individuais)"):
            zip_buffer = io.BytesIO()
            
            with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED) as zip_file:
                barra = st.progress(0)
                
                for index, row in df.iterrows():
                    doc_tpl = DocxTemplate(template_path)
                    
                    # 1. Limpeza de Dados e NaNs
                    context = {}
                    for k, v in row.to_dict().items():
                        if pd.isna(v):
                            context[k] = ""
                        elif isinstance(v, str):
                            context[k] = v.strip()
                        else:
                            context[k] = v

                    # 2. Aplicação das Formatações de Moeda e Documentos
                    context['valor_aluguel'] = formatar_moeda(context.get('valor_aluguel'))
                    context['ir_retido'] = formatar_moeda(context.get('ir_retido'))
                    
                    if 'cnpj_fonte' in context:
                        context['cnpj_fonte'] = formatar_documento(context['cnpj_fonte'])
                    if 'cpf_beneficiario' in context:
                        context['cpf_beneficiario'] = formatar_documento(context['cpf_beneficiario'])
                    
                    # --- LÓGICA DE DATA POR LINHA (REVISADA) ---
                    # Pegamos o valor bruto da coluna 'data_emissao' desta linha específica
                    valor_data_linha = row.get('data_emissao')
                    data_formatada = formatar_data_br(valor_data_linha)
                    
                    if data_formatada:
                        # Se a linha tem data, usa a data da linha formatada
                        context['data_emissao'] = data_formatada
                    else:
                        # Se a linha está vazia, usa a data padrão do sistema
                        context['data_emissao'] = "27/02/2026"

                    # 3. Renderização e Salvamento
                    doc_tpl.render(context)
                    
                    doc_io = io.BytesIO()
                    doc_tpl.save(doc_io)
                    
                    # Nomeação do Arquivo com o Nome do Beneficiário
                    nome_pessoa = str(row.get('nome_beneficiario', f"Informe_{index}")).strip().replace(" ", "_")
                    nome_arquivo_individual = f"Informe_{nome_pessoa}.docx"
                    
                    zip_file.writestr(nome_arquivo_individual, doc_io.getvalue())
                    
                    barra.progress((index + 1) / len(df))

            st.success("✅ Processamento concluído com sucesso!")
            st.download_button(
                label="📥 Baixar ZIP com Informes Nomeados",
                data=zip_buffer.getvalue(),
                file_name="Informes_Rendimentos_Custom.zip",
                mime="application/zip"
            )
                
    except Exception as e:
        st.error(f"Ocorreu um erro técnico: {e}")
