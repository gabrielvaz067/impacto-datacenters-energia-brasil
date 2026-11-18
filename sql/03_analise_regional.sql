-- Compara a carga média dos quatro subsistemas entre janeiro e outubro de 2025 e 2026.
-- Uso apenas meses completos e subsistemas com os dez meses de cada ano.

WITH resumo_anual AS (
    SELECT
        ano,
        id_subsistema,
        nom_subsistema,
        SUM(energia_gwh) AS energia_gwh,
        SUM(dias_observados) AS dias_observados
    FROM read_parquet('data/gold/carga_mensal.parquet')
    WHERE ano IN (2025, 2026)
      AND mes <= 10
      AND mes_completo = TRUE
    GROUP BY ano, id_subsistema, nom_subsistema
    HAVING COUNT(DISTINCT mes) = 10
),
comparacao AS (
    SELECT
        atual.id_subsistema,
        atual.nom_subsistema,
        -- Energia (GWh) convertida para MW médios, considerando as horas observadas.
        anterior.energia_gwh * 1000 / (anterior.dias_observados * 24) AS carga_media_2025,
        atual.energia_gwh * 1000 / (atual.dias_observados * 24) AS carga_media_2026
    FROM resumo_anual AS atual
    INNER JOIN resumo_anual AS anterior
        ON anterior.id_subsistema = atual.id_subsistema
       AND anterior.ano = 2025
    WHERE atual.ano = 2026
)
SELECT
    id_subsistema,
    nom_subsistema,
    ROUND(carga_media_2025, 2) AS carga_media_2025,
    ROUND(carga_media_2026, 2) AS carga_media_2026,
    ROUND((carga_media_2026 / NULLIF(carga_media_2025, 0) - 1) * 100, 2) AS variacao_yoy,
    ROUND(carga_media_2026 - carga_media_2025, 2) AS diferenca_mwmed
FROM comparacao
ORDER BY diferenca_mwmed DESC;
