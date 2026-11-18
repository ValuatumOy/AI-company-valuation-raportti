import pytest

from app import sensitivity


@pytest.mark.parametrize("exponents", [[1.0, 2.0, 3.0], [0.13424659, 1.13424659, 2.13424659]])
def test_terminal_growth_matrix_uses_last_forecast_date(exponents):
    """Gordon's next-year cash flow is valued at the final forecast date."""
    fcff = [-100.0, 110.0, 120.0]
    wacc, growth, bridge = 9.46, 3.0, -194.2
    discounted = [f / (1 + wacc / 100) ** t for f, t in zip(fcff, exponents)]
    terminal = fcff[-1] * (1 + growth / 100) / ((wacc - growth) / 100)
    enterprise_value = sum(discounted) + terminal / (1 + wacc / 100) ** exponents[-1]
    equity = enterprise_value + bridge

    matrix = sensitivity._wacc_growth_matrix(
        fcff, discounted, [enterprise_value], equity, wacc, bridge,
    )

    assert matrix["x_axis"] == ["2 %", "2,5 %", "3 %", "3,5 %", "4 %"]
    for row, scenario_wacc in zip(matrix["series"], [7.46, 8.46, 9.46, 10.46, 11.46]):
        for actual, scenario_growth in zip(row["values"], [2.0, 2.5, 3.0, 3.5, 4.0]):
            pv_forecast = sum(
                f / (1 + scenario_wacc / 100) ** t for f, t in zip(fcff, exponents)
            )
            terminal = fcff[-1] * (1 + scenario_growth / 100) / (
                (scenario_wacc - scenario_growth) / 100
            )
            expected = pv_forecast + terminal / (
                1 + scenario_wacc / 100
            ) ** exponents[-1] + bridge
            assert actual == round(expected)
