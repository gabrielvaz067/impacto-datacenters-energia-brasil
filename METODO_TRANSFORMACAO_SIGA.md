# Método de transformação do SIGA/ANEEL

Este arquivo descreve, em formato textual legível por máquina e por pessoas, o método atual que transforma o CSV SIGA nos resultados ANEEL do projeto. O código e os notebooks indicados são parte integrante do método; este documento não contém o CSV ou registros individuais. Sua utilização para a alternativa prevista na seção 4.6(b) da ODbL depende do uso concreto e de avaliação das condições aplicáveis.

**Atribuição:** contém informações do [SIGA — Sistema de Informações de Geração da ANEEL](https://dadosabertos.aneel.gov.br/pt_BR/dataset/siga-sistema-de-informacoes-de-geracao-da-aneel), disponibilizadas sob a [Open Database License 1.0 (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/). Os registros são validados, selecionados, convertidos, deduplicados e agregados pelo projeto. Código Python, SQL e testes autorais usam MIT; a MIT não substitui as condições ODbL nem licencia automaticamente os dados ou seus conteúdos individuais.

## Origem, fotografia e dependências

- Dataset CKAN `siga-sistema-de-informacoes-de-geracao-da-aneel`; [ficha do recurso CSV diário](https://dadosabertos.aneel.gov.br/pt_BR/dataset/siga-sistema-de-informacoes-de-geracao-da-aneel/resource/2f65a1b0-19b8-4360-8238-b34ab4693d55), recurso `2f65a1b0-19b8-4360-8238-b34ab4693d55`.
- Fotografia analisada: `DatGeracaoConjuntoDados = 2026-11-09`; manifesto `edicoes/2026-11-09.json`, 7.817.846 bytes, SHA-256 `e91c25187efbc01bc23af977b8281e2f198cf8e76a3eef198942323267836ee1`. A aquisição registrada foi em `2026-11-09T16:26:51.937861+00:00`; não confundir com a data de referência cadastral. O hash identifica bytes quando comparado a um manifesto confiável, sem comprovar autoria da fonte.
- O CSV histórico não é distribuído no Git. O catálogo atual pode fornecer outra fotografia ou revisão. Comparar hash, período e referência antes de interpretar divergências. Os objetos históricos e os notebooks/códigos daquela edição permanecem privados; os códigos e notebooks atuais têm hashes diferentes dos preservados, embora a reconstrução atual tenha reconciliado as três tabelas sobre o mesmo CSV local.
- `requirements.txt` declara Pandas, PyArrow, Requests e demais dependências; Python 3.14.7 no Windows foi testado. `src/extracao_aneel.py` usa `src/integridade_ingestao.py`, `src/rede.py` e `src/snapshots.py`; a transformação usa `src/publicacao.py`. Os notebooks 03 e 05 usam Pandas/Matplotlib; a síntese 05 também requer as camadas de carga ONS e a referência EPE. Não há arquivo auxiliar privado com regras de classificação SIGA; dados locais e controles operacionais são insumos/estado, não código do método.

## Sequência e regras

1. **Descoberta e aquisição:** `src/extracao_aneel.py`, `consultar_recurso` e `atualizar`, consulta `package_show` do CKAN e seleciona o primeiro recurso cujo nome contém `siga-empreendimentos-geracao-diario.csv` e formato é CSV. O ID acima identifica o recurso da fotografia registrada; o código atual seleciona por nome/formato, não fixa esse ID nem escolhe uma data histórica no portal. Usa hash/ETag/Last-Modified e SHA-256 local para atualização e cache; `src/rede.py` trata falhas transitórias. `--snapshot ID` é outra modalidade, offline, que requer manifesto e objetos já disponíveis; não baixa uma fotografia histórica.
2. **Validação de entrada:** `src/integridade_ingestao.py`, `validar_csv_aneel`, lê UTF-8 com BOM opcional, delimitador `;` e CSV estrito; exige cabeçalho sem duplicidade, as 12 colunas abaixo, linhas completas, pelo menos uma linha, término de linha, código/nome/fase/data de referência presentes, data de referência ISO válida e potências finitas e não negativas. A outorgada é interpretada no padrão decimal brasileiro; a fiscalizada é interpretada como decimal de ponto. `cache_valido` confere conteúdo e SHA-256 antes do reuso. Esta validação ocorre no extrator; uma chamada direta da transformação a um arquivo já colocado em Raw não a executa automaticamente.
3. **Seleção e renomeação:** `src/transformacao_aneel.py`, `COLUNAS` e `criar_silver`, lê o CSV com `pd.read_csv(sep=';', encoding='utf-8-sig', low_memory=False)` e seleciona apenas as 12 colunas a seguir, na ordem mostrada. As demais colunas do SIGA, inclusive propriedade e coordenadas, ficam fora da Silver; não há filtro por estado, fonte ou fase nessa seleção.

| Campo SIGA | Campo Silver |
|---|---|
| `CodCEG` | `cod_ceg` |
| `IdeNucleoCEG` | `id_nucleo_ceg` |
| `NomEmpreendimento` | `empreendimento` |
| `SigUFPrincipal` | `uf` |
| `SigTipoGeracao` | `tipo_geracao` |
| `DscFaseUsina` | `fase` |
| `DscOrigemCombustivel` | `origem` |
| `DscFonteCombustivel` | `fonte` |
| `MdaPotenciaFiscalizadaKw` | `potencia_fiscalizada_kw` |
| `MdaPotenciaOutorgadaKw` | `potencia_outorgada_kw` |
| `DatEntradaOperacao` | `data_operacao` |
| `DatGeracaoConjuntoDados` | `data_referencia` |

4. **Tipos e ausência:** `criar_silver` normaliza `cod_ceg` como string e remove espaços nas extremidades. Fiscalizada usa `pd.to_numeric(errors='coerce')`; outorgada usa `converter_decimal_br`, que remove pontos, troca vírgulas por pontos e converte. Código ou fase nulos/vazios e potências inválidas/nulas ou negativas bloqueiam a transformação. Datas são convertidas com `errors='coerce'`: `data_operacao` pode tornar-se nula; `data_referencia` nula ou mais de uma data normalizada após deduplicação bloqueia a publicação. Outros campos não são imputados; as Golds usam `dropna=False` nos agrupamentos. Uma chamada direta à transformação não substitui todas as verificações de integridade do extrator.
5. **CEG e deduplicação:** `tratar_duplicidades` usa `cod_ceg` como chave e compara, com `nunique(dropna=False)`, `id_nucleo_ceg`, `empreendimento`, `uf`, `tipo_geracao`, `fase`, `origem`, `fonte`, `potencia_fiscalizada_kw` e `potencia_outorgada_kw`. Se houver divergência em qualquer atributo essencial, a execução falha; se equivalentes, conserva **a primeira linha na ordem do CSV**. Datas não integram essa comparação: diferenças nelas não impedem o descarte, e a data da primeira linha é a preservada. Uma fotografia revisada ou reordenada pode, portanto, afetar campos não comparados. A unicidade final de CEG é verificada.
6. **Colunas calculadas e fases:** `criar_silver` divide as duas potências em kW por 1.000 para criar `potencia_fiscalizada_mw` e `potencia_outorgada_mw`; define `em_operacao` pela igualdade exata `fase == 'Operação'` e ordena por CEG. Outras fases são preservadas e não se tornam automaticamente projetos concluídos. Não há tabela manual de fontes adicionada à Silver.
7. **Gold por fonte:** `criar_gold_fontes` filtra `em_operacao`, agrupa por `(tipo_geracao, origem, fonte)`, com nulos incluídos, conta CEGs distintos e soma `potencia_fiscalizada_mw`. Calcula `participacao_pct = potencia_mw / soma_operacional * 100`, exige total positivo e ordena por potência decrescente. A unidade é MW; os gráficos dividem novamente por 1.000 para GW.
8. **Gold por estado:** `criar_gold_estados` mantém todas as fases, usa potência fiscalizada para `Operação` e outorgada para as demais, agrupa por `(uf, fase, tipo_geracao)` com nulos incluídos, conta CEGs distintos e soma potência em MW. Ordena pelas chaves. Não somar todas as fases como se fossem capacidade operacional homogênea.
9. **Publicação:** `main` gera `data/silver/aneel/empreendimentos.parquet`, `data/gold/aneel/capacidade_por_fonte.parquet` e `data/gold/aneel/capacidade_por_estado.parquet`, verifica soma de contagens por estado/fase contra a Silver e usa `src/publicacao.py`, classe `Publicacao`, para staging, manifesto, hashes e promoção por domínio. As cópias de compatibilidade são locais; o ponteiro versionado não é transação entre todos os domínios. Não existe etapa Processed ANEEL separada.
10. **Gráficos e tabelas:** notebook `03_analise_capacidade_aneel.ipynb`, célula de código 9 (contagem iniciada em zero), agrupa Gold de fontes por `tipo_geracao`, recalcula participação e converte MW→GW para figura 09; rótulos manuais: UHE hidrelétrica, UTE termelétrica, EOL eólica, UFV solar, PCH pequena hidrelétrica, UTN nuclear, CGH central hidrelétrica. Um código não previsto não tem rótulo nesse dicionário e requer revisão. Célula 11 filtra `Operação`, soma por UF e exibe as dez maiores na figura 10. Células 13/14 filtram exatamente `Construção` e `Construção não iniciada`, agregam por fase/tipo e somam por fase para figura 11. Célula 16 usa as mesmas fases, `pivot_table` por tipo e fase, soma, preenche combinações ausentes com zero e converte MW→GW para figura 12. Os agrupamentos dos notebooks usam os padrões do Pandas, inclusive descarte de chaves nulas onde não especificado; a Gold preserva grupos nulos. Os rótulos, filtros, arredondamento de exibição e rodapés estão no próprio notebook; as datas dos gráficos são fixas na apresentação e precisam ser revistas para um CSV atualizado.
11. **Síntese:** notebook `05_integracao_cenarios_2036.ipynb`, célula 12, soma `potencia_mw` da Gold por fonte, divide por 1.000, arredonda a duas casas e compõe a linha ANEEL da tabela `data/gold/sintese_2036.csv`. O mesmo output agrega indicadores ONS e EPE, mas não os incorpora às Silver/Gold ANEEL. `src/ingestao_epe.py` produz a referência manual EPE e não participa da transformação SIGA. Os valores EPE/ONS e hipóteses de cenários usados na síntese são conteúdos externos/adicionais identificados nos respectivos arquivos e fontes; não foram acrescentados registros ou dados externos à Silver e às duas Golds ANEEL. Rótulos, conversões e colunas calculadas são adições algorítmicas descritas acima.

O código contém a ordem de operações e `requirements.txt` as dependências diretas. As informações de data, tamanho e hash vêm do manifesto candidato ao Git; não há dependência de arquivo de controle privado para transformar um CSV já obtido. A ausência de lock completo de dependências transitivas e as diferenças entre versão histórica e atual limitam a reprodução byte a byte; o teste atual comparou conteúdos e tipos das tabelas, não provou igualdade binária de todos os Parquets.

## Execução com dados oficiais atuais

Em uma cópia nova da raiz, com dependências instaladas, `data/` vazio, rede e espaço suficientes:

```bash
python src/extracao_aneel.py --atualizar
python src/transformacao_aneel.py
python -m notebook
```

Abra o notebook 03 na raiz ou em `notebooks/` para análises e figuras 09–12. Ele lê o Raw para diagnósticos e as duas Golds para gráficos. Para executar toda a síntese do notebook 05, execute também as extrações e transformações ONS e `python src/ingestao_epe.py`, conforme [README](README.md). O resultado reflete o CSV recebido; revise datas fixas de gráficos e textos antes de apresentar uma fotografia distinta. Não é permitido interpretar o download atual como sendo necessariamente 09/11/2026.

Quem possui os objetos históricos pode usar `python src/extracao_aneel.py --snapshot 2026-11-09 --data-dir CAMINHO_VAZIO/data`, com os parâmetros de manifesto e armazenamento local conforme necessário. Esse comando restaura apenas o CSV; as transformações assumem `data/` relativo à raiz do código e precisam rodar numa cópia do projeto cuja pasta `data/` seja a área escolhida. Para a edição histórica completa, o README documenta o comando `edicoes.py reproduzir` com os objetos locais. Não há canal público do snapshot neste projeto.
