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
    """Garante o formato 6.500,00 ou 0,00 sem o R$ para evitar quebras de layout"""
    try:
        if pd.isna(valor) or valor == "" or valor == 0:
            return "0,00"
        return f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "0,00"

def formatar_documento(doc):
    """Aplica máscara de CPF ou CNPJ garantindo todos os dígitos (inclusive os últimos)"""
    # Remove qualquer carater que não seja número para evitar erros de fatiamento
    doc = re.sub(r'\D', '', str(doc)).strip()
    
    if len(doc) == 11: # CPF
        return f"{doc[:3]}.{doc[3:6]}.{doc[6:9]}-{doc[9:]}"
    elif len(doc) == 14: # CNPJ
        # O fatiamento [12:] garante que os dois últimos dígitos apareçam após o hífen
        return f"{doc[:2]}.{doc[2:5]}.{doc[5:8]}/{doc[8:12]}-{doc[12:]}"
    return doc

def localizar_template():
    """Busca automática de qualquer ficheiro .docx na raiz do repositório no GitHub"""
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
    st.success(f"✅ Template detetado: **{os.path.basename(template_path)}**")
else:
    st.error("❌ Erro: Nenhum ficheiro .docx encontrado na raiz do projeto no GitHub.")
    st.stop()

# --- INTERFACE E PROCESSAMENTO ---

uploaded_file = st.file_uploader("Carrega a tua planilha Excel", type=["xlsx"])

if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file)
        st.write(f"Registos encontrados: {len(df)}")
        st.dataframe(df.head())

        if st.button("🚀 Gerar Informes (ZIP com Nomes)"):
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

                    # 2. Aplicação das Formatações (Moeda e Documentos)
                    # Certifica-te que os nomes das colunas abaixo batem com o teu Excel
                    context['valor_aluguel'] = formatar_moeda(context.get('valor_aluguel'))
                    context['ir_retido'] = formatar_moeda(context.get('ir_retido'))
                    
                    if 'cnpj_fonte' in context:
                        context['cnpj_fonte'] = formatar_documento(context['cnpj_fonte'])
                    if 'cpf_beneficiario' in context:
                        context['cpf_beneficiario'] = formatar_documento(context['cpf_beneficiario'])
                    
                    context['data_emissao'] = "31/12/2025"

                    # 3. Renderização do Word
                    doc_tpl.render(context)
                    
                    # 4. Gravação em Memória
                    doc_io = io.BytesIO()
                    doc_tpl.save(doc_io)
                    
                    # 5. Nomeação do Ficheiro com o Nome do Beneficiário
                    nome_pessoa = str(row.get('nome_beneficiario', f"Informe_{index}")).strip().replace(" ", "_")
                    nome_ficheiro_individual = f"Informe_{nome_pessoa}.docx"
                    
                    zip_file.writestr(nome_ficheiro_individual, doc_io.getvalue())
                    
                    barra.progress((index + 1) / len(df))

            st.success("✅ Processamento concluído com sucesso!")
            st.download_button(
                label="📥 Baixar ZIP com Informes Nomeados",
                data=zip_buffer.getvalue(),
                file_name="Informes_Rendimentos_2025.zip",
                mime="application/zip"
            )
                
    except Exception as e:
        st.error(f"Ocorreu um erro técnico: {e}")
