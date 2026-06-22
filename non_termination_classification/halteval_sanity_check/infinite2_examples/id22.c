int id22(int x) {
  if ((x % 2) == 0)
    return id22(x + 2);
  return x;
}