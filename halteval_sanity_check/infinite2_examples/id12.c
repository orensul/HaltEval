void id12(unsigned int x) {
  while (x < 4)
    x++;
  while (x < 5)
    if (x == 4)
      x--;
    else
      x++;
}