void id10(unsigned int x)
{
  int toggle = 1;
  while ((x % 2) == 0)
    if (toggle) {
      x += 1;
      toggle = 0;
    }
    else {
      x -= 2;
      toggle = 1;
    }
}