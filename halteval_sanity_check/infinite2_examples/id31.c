void id31() {
  int y[10] = {0x00};
  int i = 0;
  while ((y[i] % 2) == 0) {
    i = y[i] % 10;
    y[i] += 2;
  }
}