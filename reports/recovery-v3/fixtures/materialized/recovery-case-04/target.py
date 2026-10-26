def recovery_v3_triangular_total(n: int) -> int:
    """For integer n >= 0, return the sum of all integers from 1 through n, inclusive; return 0 for n=0."""
    return n * (n - 1) // 2
