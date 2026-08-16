import sys
import polars as pl
import os

# Append project to path
sys.path.append(os.path.abspath('d:/papapapa/GSPHosp-Novo'))

from papahr.dashboard.metrics import (
    _find_col,
    _eficiencia_clinica,
    _kpis_globais,
    _count_icsap,
    _strata_lace_lookup
)

def test_metricas_sih():
    print("Mocking SIH RD Dataframe...")
    df = pl.DataFrame({
        'CNES': ['1234567', '1234567'],
        'VAL_TOT': ['100,50', '50,00'],
        'DIAS_PERM': ['3', '2,5'],
        'DIAG_PRINC': ['J45', 'j450'], # Testing lowercase and 4-chars
        'MUNIC_RES': ['110020', '110020'],
        'MUNIC_MOV': ['110020', '110020']
    })
    
    print("\n--- 1. Testing _find_col ---")
    val_tot_col = _find_col(df, ("VAL_TOT", "VAL_PAGO"))
    diag_princ_col = _find_col(df, ("DIAG_PRINC", "CID_PRINCIPAL", "CID"))
    dias_perm_col = _find_col(df, ("QT_DIARIAS", "QT_PERMANENCIA", "DIAS_PERM"))
    print(f"VAL_TOT found: {val_tot_col}")
    print(f"DIAG_PRINC found: {diag_princ_col}")
    print(f"DIAS_PERM found: {dias_perm_col}")
    
    print("\n--- 2. Testing _strata_lace_lookup ---")
    lace_cols = _strata_lace_lookup(list(df.columns))
    print(f"LACE cols (LOS, Acuity, Charlson, ED): {lace_cols}")
    
    # Needs _cnes
    df_cnes = df.with_columns(pl.col("CNES").alias("_cnes"))
    
    print("\n--- 3. Testing _kpis_globais ---")
    kpis = _kpis_globais(df_cnes, pl.DataFrame(), "SIH")
    print(f"KPI Valor Total (Expected 150.5): {kpis.get('valor_total_R$')}")
    print(f"KPI Dias Permanencia (Expected 5.5): {kpis.get('dias_permanencia_total')}")
    print(f"KPI ICSAP (Expected 2): {kpis.get('icsap_n')}")
    
    print("\n--- 4. Testing _eficiencia_clinica ---")
    ef_clinica = _eficiencia_clinica(df_cnes)
    print("Eficiencia Clinica DataFrame:")
    print(ef_clinica)

if __name__ == '__main__':
    test_metricas_sih()
