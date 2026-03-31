## Simulador de Tarifas e Poupança (Portugal)

Este projeto cria a base de um simulador em Python/Streamlit para:

- Modelar a estrutura tarifária portuguesa (ciclos semanal, semanal opcional e diário, com verão/inverno).
- Ler diagramas de carga da E-Redes (ficheiros anuais com 35 040 registos de 15 em 15 minutos).
- Atribuir cada registo de consumo a um dos 4 períodos (`ponta`, `cheias`, `vazio`, `super_vazio`).
- Calcular custos de energia e potência, incluindo o fator de horas de ponta por mês.
- Permitir futura comparação entre períodos atuais e novos períodos (consulta pública 137 / 2027).

### Como correr localmente

1. Crie e ative um ambiente virtual (opcional mas recomendado).
2. Instale as dependências:

```bash
pip install -r requirements.txt
```

3. Execute a aplicação Streamlit:

```bash
streamlit run app.py
```

### Estrutura inicial

- `tariff_calendar.py` — lógica dos ciclos horários e calendário (inclui feriados principais em Portugal).
- `pricing.py` — funções de cálculo de energia, potência e fator de horas de ponta.
- `load_profile.py` — leitura e preparação do diagrama de carga da E-Redes.
- `app.py` — interface Streamlit para carregar ficheiros, escolher ano/ciclo e inserir tarifas.

Nesta primeira fase a ênfase está na estrutura e na modelação da tarifa atual. A camada de comparação com os novos períodos de 2027 será ligada assim que tivermos os horários completos extraídos da documentação oficial.

