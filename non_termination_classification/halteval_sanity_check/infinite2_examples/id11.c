void id11(unsigned int x) {
  while (x < 5)
    x++;
  while (x < 5)
    if (x == 3)
      x--;
    else
      x++;
}