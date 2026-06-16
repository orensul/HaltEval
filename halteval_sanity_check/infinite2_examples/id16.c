void id16(int x) {
  while (x < 5) {
    x++;
    while (x < 6)
      x++;
  }
  while ((x % 2) == 0)
    x += 2;
}