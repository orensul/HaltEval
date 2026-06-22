void id31(int y, int x){
  int turn = 0;
  while (x < 100) {
    if (turn)
      x++;
    else
      x = x + 2;
    turn = (turn ? 0 : 1);
  }
}