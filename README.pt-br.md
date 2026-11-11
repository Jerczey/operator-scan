# Operator Lifecycle Scanner - Documentação

Aplicação web que analisa um cluster OpenShift em busca de operadores OLM instalados e reporta a fase de ciclo de vida de suporte da Red Hat (Full Support, Maintenance Support ou Out of maintenance).

## Contexto

Cada operador Red Hat segue uma classificação de ciclo de vida (Platform Aligned, Platform Agnostic, Rolling Stream ou Product Aligned). Administradores precisam saber quais versões instaladas ainda estão em suporte pleno ou em manutenção.

Esta ferramenta lista Subscriptions / ClusterServiceVersions e compara com um catálogo local derivado das páginas de Operator Life Cycles, com atualização opcional via Product Life Cycle Data API.

## Como funciona

1. Conecta via ServiceAccount in-cluster ou kubeconfig.
2. Lista `subscriptions` e `clusterserviceversions`.
3. Associa pacote + versão ao catálogo.
4. Calcula a fase atual pelas datas de fim de suporte.
5. Salva relatórios JSON/CSV.

## Deploy

Veja [README.md](README.md) para build e deploy no OpenShift.

## Referências

- [OpenShift Operator Life Cycles](https://access.redhat.com/support/policy/updates/openshift_operators)
- [Product Life Cycle Data API](https://docs.redhat.com/en/documentation/red_hat_product_life_cycle_data_api/1.0)
