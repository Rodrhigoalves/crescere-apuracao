import pandas as pd

# Para cada arquivo, rode separadamente:
df = pd.read_excel('caminho/do/arquivo.xlsx', header=None, dtype=str)

print("=" * 80)
print("ARQUIVO: nome_do_arquivo.xlsx")
print("=" * 80)
print(f"Shape: {df.shape}")
print(f"\nColunas: {df.shape[1]} | Linhas: {df.shape[0]}")
print("\n" + "-" * 80)
print("PRIMEIRAS 20 LINHAS COMPLETAS:")
print("-" * 80)
print(df.head(20).to_string())
