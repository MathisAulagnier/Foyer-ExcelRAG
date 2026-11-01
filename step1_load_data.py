"""
Étape 1 : Chargement et interrogation des données avec DuckDB
------------------------------------------------------------
Ce script a pour but de :
1. Charger les feuilles du fichier Excel dans des DataFrames pandas.
2. Déposer ces DataFrames dans un moteur SQL in-memory ultra-rapide (DuckDB).
3. Exécuter des requêtes SQL réelles (agrégation, calculs, jointures).
"""

from pathlib import Path
import pandas as pd
import duckdb

# 1. Définition du chemin vers le fichier Excel
BASE_DIR = Path(__file__).resolve().parent
EXCEL_PATH = BASE_DIR / "data" / "frtr_0021_insurance-claims.xlsx"

print(f"Chargement du fichier : {EXCEL_PATH.name}...")

# 2. Lecture des feuilles avec pandas
# Attention : dans ce fichier, la ligne 0 contient une note textuelle.
# On utilise donc 'header=1' pour indiquer que les vrais noms de colonnes sont sur la 2e ligne.
df_claims = pd.read_excel(EXCEL_PATH, sheet_name="Claims", header=1)
df_payments = pd.read_excel(EXCEL_PATH, sheet_name="Payments", header=1)
df_summary = pd.read_excel(EXCEL_PATH, sheet_name="LOB_Summary", header=1)

print(f"-> 'claims' chargé : {len(df_claims):,} lignes")
print(f"-> 'payments' chargé : {len(df_payments):,} lignes")

# 3. Connexion à une base DuckDB en mémoire (RAM)
# DuckDB s'exécute directement dans le processus Python (aucun serveur à installer).
con = duckdb.connect(database=":memory:")

# 4. Enregistrement des tables dans DuckDB
con.register("claims", df_claims)
con.register("payments", df_payments)
con.register("lob_summary", df_summary)

print("\nTables disponibles dans DuckDB :", [row[0] for row in con.execute("SHOW TABLES").fetchall()])

# 5. Démonstration 1 : Agrégation SQL (Top branches par montant de sinistre payé)
query_agg = """
SELECT 
    LOB AS branche,
    COUNT(*) AS nombre_sinistres,
    ROUND(SUM(PaidUSD), 2) AS total_paye_usd,
    ROUND(AVG(PaidUSD), 2) AS moyenne_paye_usd
FROM claims
GROUP BY LOB
ORDER BY total_paye_usd DESC;
"""

print("\n--- Résultat : Agrégation par branche (LOB) ---")
result_agg = con.execute(query_agg).df()
print(result_agg)

# 6. Démonstration 2 : Jointure relationnelle entre Claims et Payments (sur PolicyID)
query_join = """
SELECT 
    c.PolicyID,
    c.LOB,
    c.Status,
    c.PaidUSD AS sinistre_declare,
    p.AmountUSD AS montant_regle
FROM claims c
INNER JOIN payments p ON c.PolicyID = p.PolicyID
LIMIT 5;
"""

print("\n--- Résultat : Jointure entre sinistres et règlements (Top 5) ---")
result_join = con.execute(query_join).df()
print(result_join)
