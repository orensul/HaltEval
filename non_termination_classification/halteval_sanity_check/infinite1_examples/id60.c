int id60(int x, int y){
  if (x > 0) {
    int n;
    if (y > 0) {
      y--;
      n = id60(x, y);
    } else {
      n = 1;
    }
    x--;
    return id60(x, n);
  } else {
    return y + 1;
  }
}