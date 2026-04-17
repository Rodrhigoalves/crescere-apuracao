import streamlit as st
import pandas as pd
from docxtpl import DocxTemplate
import io
import zipfile
import os

# Configuração da página
st.set_page_config(page_title="Gerador de Informes", layout="wide")

st.title("📄 Gerador de Informe de Rendimentos")
st.markdown("---")

# 1. LOCALIZAÇÃO DO TEMPLATE (Ajustado conforme seu print)
# Como o script está em /pages, precisamos subir um nível para achar o .docx na raiz
current_dir = os.path.dirname(os.path.abspath(__file__))
template_path = os.path.join(current_dir, "..", "INFORME-RENDIMENTO-EDITAVEL.docx")

# 2. INTERFACE DE UPLOAD DA PLANILHA
st.subheader("1. Selecione a planilha de dados")
uploaded_file = st.file_uploader("Arraste o arquivo Aluguel.xlsx aqui", type=["xlsx"])

if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file)
        st.success("✅ Planilha carregada com sucesso!")
        st.dataframe(df.head(10)) # Mostra as primeiras 10 linhas para conferência

        # 3. VERIFICAÇÃO DO TEMPLATE NO SERVIDOR
        if not os.path.exists(template_path):
            st.error(f"❌ Erro: O arquivo '{os.path.basename(template_path)}' não foi encontrado na raiz do projeto.")
            st.info(f"Caminho tentado: {template_path}")
        else:
            st.subheader("2. Gerar Documentos")
            if st.button("🚀 Gerar todos os Informes em ZIP"):
                
                # Criar o arquivo ZIP na memória
                zip_buffer = io.BytesIO()
                
                with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED) as zip_file:
                    progress_bar = st.progress(0)
                    total_rows = len(df)
                    
                    for index, row in df.iterrows():
                        # Carrega o template a cada iteração
                        doc = DocxTemplate(template_path)
                        
                        # Converte a linha do Excel para o dicionário de tags do Word
                        context = row.to_dict()
                        
                        # Define a data fixa de emissão (último dia do ano anterior)
                        context['data_emissao'] = "31/12/2025"
                        
                        # Preenche o Word (renderiza)
                        doc.render(context)
                        
                        # Salva o arquivo preenchido em memória
                        doc_io = io.BytesIO()
                        doc.save(doc_io)
                        doc_io.seek(0)
                        
                        # Define o nome do arquivo individual dentro do ZIP
                        nome_beneficiario = str(row['nome_beneficiario']).strip().replace(" ", "_")
                        file_name = f"Informe_{nome_beneficiario}.docx"
                        
                        # Adiciona ao ZIP
                        zip_file.writestr(file_name, doc_io.getvalue())
                        
                        # Atualiza barra de progresso
                        progress_bar.progress((index + 1) / total_rows)

                # Botão de Download do arquivo final
                st.success(f"✨ {total_rows} Informes gerados com sucesso!")
                st.download_button(
                    label="📥 Baixar todos os Informes (.zip)",
                    data=zip_buffer.getvalue(),
                    file_name="Informes_Gerados_Aluguel.zip",
                    mime="application/zip"
                )
                
    except Exception as e:
        st.error(f"Erro ao processar a planilha: {e}")
else:
    st.info("Aguardando o upload da planilha Excel para liberar o gerador.")
