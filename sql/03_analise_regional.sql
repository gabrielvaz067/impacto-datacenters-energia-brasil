
-- Análise regional da carga elétrica
-- Comparação janeiro a outubro de 2025 e 2026

WITH base AS (
    SELECT
        ano,
        id_subsistema,
        nom_subsistema,
        SUM(energia_gwh) AS energia_gwh,
        SUM(dias_observados) AS dias_observados
    FROM read_parquet(
        'data/gold/carga_mensal.parquet'
    )
    WHERE mes <= 10
      AND mes_completo = TRUE
      AND ano IN (2025, 2026)
    GROUP BY
        ano,
        id_subsistema,
        nom_subsistema
    HAVING COUNT(DISTINCT mes) = 10
),

comparacao AS (
    SELECT
        atual.id_subsistema,
        atual.nom_subsistema,

        anterior.energia_gwh AS energia_2025_gwh,
        atual.energia_gwh AS energia_2026_gwh,

        anterior.dias_observados AS dias_2025,
        atual.dias_observados AS dias_2026,

        anterior.energia_gwh * 1000 /
            (anterior.dias_observados * 24)
            AS carga_media_2025,

        atual.energia_gwh * 1000 /
            (atual.dias_observados * 24)
            AS carga_media_2026

    FROM base atual
    INNER JOIN base anterior
        ON atual.id_subsistema = anterior.id_subsistema
       AND anterior.ano = 2025
    WHERE atual.ano = 2026
)

SELECT
    id_subsistema,
    nom_subsistema,

    ROUND(carga_media_2025, 2)
        AS carga_media_2025,

    ROUND(carga_media_2026, 2)
        AS carga_media_2026,

    ROUND(
        (carga_media_2026 /
         NULLIF(carga_media_2025, 0) - 1) * 100,
        2
    ) AS variacao_yoy,

    ROUND(
        carga_media_2026 - carga_media_2025,
        2
    ) AS diferenca_mwmed

FROM comparacao

ORDER BY diferenca_mwmed DESC;
