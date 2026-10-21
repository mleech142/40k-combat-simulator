"""
Play mode: full games on a top-down board (design: docs/play-mode-design.md).

Game dice live here. They use simulation.resolve() for what each roll needs, and never
the analytical or Monte Carlo functions, which stay for the Simulator and Analytics.
"""
