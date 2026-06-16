void id29() {
  int x = 7;
  while (x > 0) {
  restart:
    x++;
    if (x == 10)
      goto restart;
    x -= 2;
  }
}