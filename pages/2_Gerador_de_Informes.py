import streamlit as st
import pandas as pd
from docxtpl import DocxTemplate
import io
import zipfile
import os

# Configuração da página para o teu ASUS TUF (Modo Largo)
st.set_page_config(page_title="Gerador de Informes Pro", layout="wide")

st.title("📄 Gerador de Informe de Rendimentos")
st.markdown("---")

# --- FUNÇÕES DE FORMATAÇÃO TÉCNICA ---

def formatar_moeda(valor):
    """Formata valores para o padrão 6.500,00 ou 6.500,10 sem o R$"""
    try:
        if pd.isna(valor) or valor == "" or valor == 0:
            return "0,00"
        return f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return valor

def formatar_documento(doc):
    """Aplica máscara de CPF (11 dígitos) ou CNPJ (14 dígitos) automaticamente"""
    doc = str(doc).translate(str.maketrans("", "", "./- ")) # Remove formatação existente
    if len(doc) == 11: # CPF
        return f"{doc[:3]}.{doc[3:6]}.{doc[6:9]}-{doc[9:]}"
    elif len(doc) == 14: # CNPJ
        return f"{doc[:2]}.{doc[2:5]}.{doc[5:8]}/{doc[8:12]}-{doc[12:]}"
    return doc

def localizar_template():
    """Procura qualquer ficheiro .docx na raiz do projeto (um nível acima de /pages)"""
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
    st.success(f"✅ Template detetado no GitHub: **{os.path.basename(template_path)}**")
else:
    st.error("❌ Erro: Nenhum ficheiro Word (.docx) encontrado na raiz do projeto.")
    st.stop()

# --- INTERFACE DE UPLOAD ---
st.subheader("1. Dados de Entrada")
uploaded_file = st.file_uploader("Carrega a tua planilha Excel (Aluguel.xlsx)", type=["xlsx"])

if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file)
        st.write(f"Registos encontrados: {len(df)}")
        st.dataframe(df.head())

        st.subheader("2. Processamento")
        if st.button("🚀 Gerar Informes em Lote (ZIP)"):
            
            zip_buffer = io.BytesIO()
            
            with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED) as zip_file:
                barra_progresso = st.progress(0)
                
                for index, row in df.iterrows():
                    doc_tpl = DocxTemplate(template_path)
                    
                    # Converte a linha da planilha em dicionário
                    context = row.to_dict()
                    
                    # --- APLICAÇÃO DAS REGRAS DE FORMATAÇÃO ---
                    
                    # 1. Valores Monetários (ajusta os nomes das colunas conforme o teu Excel)
                    campos_valor = ['valor_aluguel', 'ir_retido']
                    for campo in campos_valor:
                        context[campo] = formatar_moeda(context.get(campo, 0))
                    
                    # 2. Documentos (CPF/CNPJ)
                    if 'cnpj_fonte' in context:
                        context['cnpj_fonte'] = formatar_documento(context['cnpj_fonte'])
                    if 'cpf_beneficiario' in context:
                        context['cpf_beneficiario'] = formatar_documento(context['cpf_beneficiario'])
                    
                    # 3. Data e Limpeza de NaNs
                    context['data_emissao'] = "31/12/2025"
                    # Garante que qualquer outro campo vazio não escreva 'nan' no Word
                    context = {k: ("" if pd.isna(v) else v) for k, v in context.items()}
                    
                    # Renderiza o preenchimento mantendo as bordas e estilos
                    doc_tpl.render(context)
                    
                    # Guarda o documento preenchido em memória
                    doc_io = io.BytesIO()
                    doc_tpl.save(doc_io)
                    doc_io.seek(0)
                    
                    # Nome do ficheiro dentro do ZIP
                    nome_cliente = str(row.get('nome_beneficiario', f"Informe_{index}")).strip().replace(" ", "_")
                    zip_file.writestr(f"Informe_{nome_cliente}.docx", doc_io.getvalue())
                    
                    # Atualiza progresso
                    barra_progresso.progress((index + 1) / len(df))

            st.success("✅ Todos os documentos foram gerados e formatados!")
            st.download_button(
                label="📥 Baixar Ficheiros ZIP",
                data=zip_buffer.getvalue(),
                file_name="Informes_Rendimentos_Formatados.zip",
                mime="application/zip"
            )
                
    except Exception as e:
        st.error(f"Ocorreu um erro no processamento: {e}")
