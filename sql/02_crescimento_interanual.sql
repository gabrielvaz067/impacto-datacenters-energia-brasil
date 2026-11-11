
WITH carga_mensal AS (
    SELECT
        ano,
        mes,
        SUM(energia_gwh) AS energia_gwh
    FROM read_parquet('data/gold/carga_mensal.parquet')
    WHERE mes_completo = TRUE
    GROUP BY ano, mes
    HAVING COUNT(DISTINCT id_subsistema) = 4
)

SELECT
    atual.ano,
    atual.mes,
    ROUND(atual.energia_gwh, 2) AS energia_gwh,
    ROUND(anterior.energia_gwh, 2) AS energia_ano_anterior_gwh,
    ROUND(
        (
            atual.energia_gwh /
            NULLIF(anterior.energia_gwh, 0) - 1
        ) * 100,
        2
    ) AS variacao_yoy
FROM carga_mensal atual
LEFT JOIN carga_mensal anterior
    ON atual.ano = anterior.ano + 1
    AND atual.mes = anterior.mes
ORDER BY atual.ano, atual.mes;
