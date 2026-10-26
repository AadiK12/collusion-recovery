def recovery_v3_last_digit_abs(n: int) -> int:
    """For any integer n, return the last decimal digit of its absolute value."""
    return abs(n) % 9
