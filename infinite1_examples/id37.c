int id37(int x, int y){
re:
  x++;
  if (y) {
    int z1 = x * 2;
    goto re;
    return (z1);
  } else {
    int z2 = x + y;
    return z2;
  }
}