int id24(int x) {
  if (x > 50 && x < 100)
    return id24(x + 4);
  else
    return id24(x + 2);
  return x;
}