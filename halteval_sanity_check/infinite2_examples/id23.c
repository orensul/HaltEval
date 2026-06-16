int id23(int x) {
  if ((x % 2) == 0)
    return id23(x + 1);
  return x;
}