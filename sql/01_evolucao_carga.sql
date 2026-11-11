
-- Evolução mensal da energia associada à carga do SIN
-- Apenas meses completos

SELECT
    ano,
    mes,
    MAKE_DATE(ano, mes, 1) AS competencia,
    ROUND(SUM(energia_gwh), 2) AS energia_gwh,
    ROUND(AVG(carga_media_mwmed), 2) AS media_subsistemas_mwmed
FROM read_parquet(
    'data/gold/carga_mensal.parquet'
)
WHERE mes_completo = TRUE
GROUP BY
    ano,
    mes
HAVING COUNT(DISTINCT id_subsistema) = 4
ORDER BY
    ano,
    mes;
