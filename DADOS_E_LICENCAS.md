# Dados, atribuição e distribuição

Verificação das páginas e documentos indicados: **10–11/11/2026**. Esta política prepara uma possível publicação; ela não concede licenças em nome dos órgãos nem confirma que arquivos locais já estejam disponíveis publicamente. A aplicação das condições a cada derivado e eventual conteúdo de terceiros ainda requer revisão antes da divulgação.

## Fontes e condições identificadas

### ONS — carga diária e geração horária

Os catálogos oficiais de [carga de energia](https://dados.ons.org.br/dataset/carga-energia) e [geração por usina](https://dados.ons.org.br/dataset/geracao-usina-2) identificam **Creative Commons Atribuição (CC BY)**. Os avisos das duas páginas permitem distribuição e adaptação com crédito ao ONS e indicação de alterações; os dados podem ser revisados. As páginas consultadas não identificam de forma inequívoca a versão da CC BY: o link exibido conduz à Open Definition, e não ao texto específico da licença. Confirmar a versão aplicável com o ONS antes de declarar uma versão concreta em uma distribuição.

O projeto transforma carga diária em Silver e agregados mensais e agrega geração horária, preservando nulos e negativos. Ao redistribuir dados ONS ou derivados, manter a identificação da fonte, endereço, licença aplicável após confirmação e descrição dessas transformações, além dos avisos exigidos pela licença pertinente. A disponibilização do portal não garante que a fotografia antiga continue acessível.

### ANEEL — SIGA

O [catálogo SIGA](https://dadosabertos.aneel.gov.br/dataset/siga-sistema-de-informacoes-de-geracao-da-aneel) e a [ficha do CSV diário](https://dadosabertos.aneel.gov.br/dataset/siga-sistema-de-informacoes-de-geracao-da-aneel/resource/2f65a1b0-19b8-4360-8238-b34ab4693d55) identificam a **Open Data Commons Open Database License (ODbL)**. O [texto ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) prevê avisos de origem e licença, condições de compartilhamento para bases derivadas publicamente utilizadas e, em situações previstas, acesso à base derivada ou ao método de alteração. Obras produzidas e bases derivadas têm tratamentos distintos; o texto e seu enquadramento concreto precisam ser verificados para cada distribuição. A licença de banco de dados não substitui a análise de direitos sobre conteúdos individuais ou dados pessoais eventualmente presentes.

**Atribuição proposta para resultados do SIGA:** contém informações do [SIGA — Sistema de Informações de Geração da ANEEL](https://dadosabertos.aneel.gov.br/dataset/siga-sistema-de-informacoes-de-geracao-da-aneel), disponibilizado sob a [ODbL](https://opendatacommons.org/licenses/odbl/1-0/). Neste projeto os registros são selecionados, deduplicados após validação e agregados por fonte, estado e fase. O código descreve o método; a fotografia histórica específica ainda não está publicamente distribuída.

Antes de disponibilizar Silver, Gold, ZIP ou outra base baseada no SIGA, revisar a classificação de cada artefato, as seções 4.2–4.7 da ODbL, a forma de fornecer base ou alterações quando aplicável, avisos e eventuais dados pessoais. A consulta à fonte atual não garante os bytes antigos.

A seção 4.3 também prevê aviso associado à obra produzida e publicamente utilizada; os notebooks com resultados SIGA e as figuras 09–12 identificam a base e a ODbL. A seção 4.6 prevê, quando aplicável, oferecer a base derivada em formato legível por máquina **ou** um arquivo com todas as alterações ou o método de produzi-las, inclusive conteúdos adicionais que componham as diferenças. O [método SIGA](METODO_TRANSFORMACAO_SIGA.md) mapeia os scripts, parâmetros, regras e conteúdos adicionais; a reconstrução local reconciliou Silver e Gold. O enquadramento jurídico depende do uso concreto e das condições aplicáveis. A descrição do método não torna o snapshot SIGA de 09/11/2026 acessível publicamente nem comprova, isoladamente, conformidade jurídica para todo uso.

### EPE — PDE 2036

A [página oficial do PDE 2036](https://www.epe.gov.br/pt/publicacoes-dados-abertos/publicacoes/plano-decenal-de-expansao-de-energia-2036) identifica a versão para consulta pública. As fontes dos indicadores são o [Relatório Síntese, outubro/2026](https://www.epe.gov.br/sites-pt/publicacoes-dados-abertos/publicacoes/PublicacoesArquivos/publicacao-982/topico-860/PDE%202036_Relat%C3%B3rio%20S%C3%ADntese_rev3_2026-10-08.pdf), Tabela 2-4 e Figura 2-12, páginas impressas 59–60, e o [Caderno de Demanda](https://www.epe.gov.br/sites-pt/publicacoes-dados-abertos/publicacoes/PublicacoesArquivos/publicacao-982/topico-851/PDE%202036_Caderno%20de%20Demanda%20de%20Energia_v1_2026-10-27.pdf), páginas 11 e 36.

O projeto mantém uma **curadoria manual de quatro indicadores e seus metadados**, gerada por `src/ingestao_epe.py`; não baixa os PDFs como parte dessa curadoria. Em 11/11/2026, o rodapé da [página oficial do PDE 2036](https://epe.gov.br/pt/publicacoes-dados-abertos/publicacoes/plano-decenal-de-expansao-de-energia-2036) declarava todo o conteúdo do site sob [Creative Commons Atribuição 4.0 Internacional](https://creativecommons.org/licenses/by/4.0/). Essa é uma evidência positiva de licenciamento do conteúdo do site. Os textos pesquisáveis dos dois PDFs específicos não identificaram uma cláusula de licença própria; o alcance do aviso do site sobre cada material, em especial conteúdo de terceiros eventualmente incorporado, deve ser avaliado sem presumir proibição ou autorização para todos os direitos. Cite MME/EPE, título, versão, páginas, licença indicada e alterações. Os gráficos do projeto são próprios, feitos a partir de valores curados, e não reproduções integrais dos PDFs. Os PDFs originais não estão no ZIP histórico analisado.

## Política proposta de arquivos

| Grupo | Tratamento proposto |
|---|---|
| Código Python, SQL e testes autorais | Licenciados sob a [MIT](LICENSE), copyright © 2026 Gabriel Gomes Vaz. A distribuição continua sujeita aos direitos de terceiros e aos termos das fontes incorporadas. |
| Notebooks, imagens e documentação atuais | Candidatos após revisão de direitos e atribuição das fontes. Gráficos e outputs são resultados do projeto, mas podem incorporar dados ou direitos de terceiros; não recebem automaticamente a licença do código. |
| `data/reference/` | Fora do Git por padrão. O CSV EPE é uma curadoria derivada e pode ser gerado localmente com `python src/ingestao_epe.py`; os quatro valores também aparecem no código e resultados e exigem a revisão EPE indicada acima. |
| `data/raw/`, `data/processed/`, `data/silver/` e `data/gold/` | Fora do Git por padrão. São obtidos ou reconstruídos localmente; não incluir por força do comando `git add -f` antes da avaliação de atribuição, licença, direitos de terceiros e dados pessoais. |
| `data/controle_*.json`, `data/.publicacoes/`, ambientes virtuais e execução temporária | Operacionais e fora do Git. Seus metadados podem conter URLs, versões e identificadores locais; não são substitutos do manifesto histórico. |
| `edicoes/2026-11-09.json` e `config/edicao_2026-11-09.json` | Candidatos ao Git como metadados, após revisão; não contêm os objetos necessários por si só. A edição registra fontes, recursos, referência, aquisição conhecida, caminhos relativos, tamanhos e SHA-256. |
| `data/snapshots/` e `pacotes/` | Preservados localmente e fora do Git. O ZIP contém insumos de terceiros e notebooks históricos com caminhos pessoais; **não está aprovado para distribuição**. |

O `.gitignore` apenas evita inclusão acidental; não apaga arquivos nem impede inclusão forçada. Os itens acima são uma **proposta**, não uma lista de arquivos já publicados. Neste workspace, antes de qualquer commit, somente `README.md` estava rastreado; os demais arquivos candidatos aparecem como não rastreados. Para registrar uma versão pública, revisar explicitamente o conteúdo a incluir e testar um clone dessa versão.

## Reprodução e transporte da edição histórica

O manifesto `edicoes/2026-11-09.json` identifica 27 objetos por tamanho e SHA-256. Ele permite detectar alteração dos objetos ao ser comparado com uma cópia confiável do manifesto; por si só não prova origem ou autenticidade. A edição usa o código e os insumos preservados, não as versões correntes dos portais.

O ZIP local `pacotes/2026-11-09.zip` tem 11.335.355 bytes e SHA-256 `62750e2c31edba1e40d2c9793bc961489a34c1974e3a2085eac82d56c6e0a5ab`, conferido na validação local. **O manifesto histórico registra hashes dos objetos, não do ZIP inteiro.** O pacote contém o manifesto e 27 objetos por hash; o importador compara o manifesto fornecido separadamente e confere tamanho e hash de cada objeto.

Quando os objetos forem obtidos por um canal autorizado e uma cópia confiável do manifesto estiver disponível, a partir da raiz do projeto:

```bash
python src/edicoes.py importar --manifesto edicoes/2026-11-09.json --pacote CAMINHO_DO_PACOTE.zip --armazenamento data/snapshots
python src/edicoes.py verificar --manifesto edicoes/2026-11-09.json --armazenamento data/snapshots
python src/edicoes.py reproduzir --manifesto edicoes/2026-11-09.json --armazenamento data/snapshots --destino execucoes/historica
```

Esses comandos não tornam o pacote acessível a terceiros. O pacote não foi publicado; uma futura distribuição exige análise das licenças, dos avisos ODbL/ONS, dos dados e dos caminhos pessoais contidos nos notebooks arquivados. Não alterar o ZIP original: se a análise autorizar uma distribuição, avaliar um **pacote público separado**, com manifesto próprio, instruções e revisão de privacidade, preservando em paralelo a edição original. Alterar bytes dos notebooks impede que o pacote modificado seja idêntico à edição histórica e exige identificá-lo como variante, com suas limitações.

Para atualização operacional, os scripts consultam os recursos correntes e registram hashes/versões recebidos. Essa atualização pode conter revisões e não equivale à fotografia de 2026-11-09. Sem os objetos históricos, um clone não reproduz a edição exata; não é possível recuperar bytes antigos por hashes ou pela simples consulta ao portal.

## Código, gráficos e documentos

Por autorização expressa do responsável, o código Python, os scripts SQL e os testes **autorais** estão licenciados sob a [MIT](LICENSE), copyright © 2026 Gabriel Gomes Vaz. O texto oficial exige manter o aviso de copyright e de permissão nas cópias ou partes substanciais do software. A MIT do projeto não abrange automaticamente datasets ONS/ANEEL/EPE, documentos de terceiros, outputs de notebooks, gráficos ou demais conteúdos com direitos próprios; valores e referências incorporados ao código permanecem sujeitos às condições pertinentes das fontes.

As bibliotecas externas instaladas conforme `requirements.txt` são componentes de terceiros e mantêm suas próprias licenças e avisos. A revisão de imports e avisos em `src/`, `sql/` e `tests/` não identificou código de terceiros incorporado com aviso próprio; isso não prova autoria de cada trecho. Metadados locais identificam, por exemplo, pandas e ipykernel com BSD-3-Clause, PyArrow com Apache-2.0 e arquivos LICENSE/NOTICE, e Requests com Apache-2.0 e arquivos LICENSE/NOTICE. Antes de redistribuir bibliotecas ou seus componentes transitivos, confira e acompanhe os avisos e as licenças das versões efetivamente distribuídas. Dados e curadoria EPE exigem a revisão específica já descrita acima.

O mesmo cuidado vale para notebooks, textos, figuras e resultados: verificar direitos autorais próprios, atribuições e obrigações das bases utilizadas. Não atribuir uma licença geral a esses materiais enquanto sua abrangência e os direitos de terceiros não tiverem sido decididos. A EPE mantém seus documentos nas fontes oficiais; este projeto aponta para essas fontes em vez de redistribuir os PDFs.

### Natureza dos artefatos

Os valores numéricos e fatos são apresentados como indicadores extraídos ou calculados, com fonte e perímetro; isso não resolve por si só direitos sobre uma base ou documento. O código autoral executa transformações, enquanto Raw e tabelas derivadas são conjuntos de dados sujeitos a suas próprias condições. Os gráficos são construídos pelo projeto a partir desses dados e não são automaticamente cópias integrais das figuras originais; isso também não elimina a necessidade de atribuição e revisão das condições aplicáveis. Não foram identificados arquivos visuais externos incorporados na geração das 21 figuras; as fontes são os dados curados e os gráficos gerados nos notebooks.

Na versão atual de apresentação, amostras individuais desnecessárias do SIGA e da geração foram substituídas por diagnósticos agregados. Nome de empreendimento, código, coordenada ou indicação de propriedade pode descrever pessoa jurídica ou instalação; sua presença não implica automaticamente que seja dado pessoal protegido. A natureza concreta do conteúdo e de eventuais identificáveis deve ser avaliada antes da divulgação.
