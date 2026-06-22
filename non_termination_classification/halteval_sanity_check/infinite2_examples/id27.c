void id27(int p, int b, int e)
{
  while (p != 1) {
    p = (p * b) % e;
  }
}