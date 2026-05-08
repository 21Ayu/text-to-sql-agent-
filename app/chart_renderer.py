"""
Renders Plotly charts from a DataFrame based on chart_type, x_axis, and y_axis.
Returns a Plotly Figure object (rendered in Streamlit via st.plotly_chart).
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


def render_chart(
    df: pd.DataFrame,
    chart_type: str,
    x_axis: str,
    y_axis: str,
    title: str = "",
) -> go.Figure:
    """
    Dispatches to the correct Plotly chart builder.
    Falls back to a bar chart if chart_type is unrecognized.
    """
    chart_type = chart_type.lower()

    if x_axis not in df.columns or y_axis not in df.columns:
        raise ValueError(
            f"Columns '{x_axis}' or '{y_axis}' not found in result. "
            f"Available columns: {list(df.columns)}"
        )

    if chart_type == "bar":
        fig = px.bar(df, x=x_axis, y=y_axis, title=title, text_auto=True)

    elif chart_type == "line":
        fig = px.line(df, x=x_axis, y=y_axis, title=title, markers=True)

    elif chart_type == "pie":
        fig = px.pie(df, names=x_axis, values=y_axis, title=title)

    elif chart_type == "scatter":
        fig = px.scatter(df, x=x_axis, y=y_axis, title=title)

    elif chart_type == "area":
        fig = px.area(df, x=x_axis, y=y_axis, title=title)

    else:
        # Default fallback
        fig = px.bar(df, x=x_axis, y=y_axis, title=title, text_auto=True)

    fig.update_layout(
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(size=13, color="#111111"),
        title_font=dict(size=16, color="#111111"),
        margin=dict(t=50, l=40, r=20, b=40),
    )
    fig.update_xaxes(
        title_font=dict(size=13, color="#111111"),
        tickfont=dict(size=12, color="#111111"),
    )
    fig.update_yaxes(
        title_font=dict(size=13, color="#111111"),
        tickfont=dict(size=12, color="#111111"),
    )
    return fig
