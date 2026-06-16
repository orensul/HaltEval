void id30(int y, int x){
  int turn = 0;
  while (x < 100) {
    if (turn)
      x++;
    else
      x--;
    turn = (turn ? 0 : 1);
  }
}