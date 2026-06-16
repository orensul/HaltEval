int id25(int x) {
  if (x < 100)
    return id25(x + 2);
  return x;
}